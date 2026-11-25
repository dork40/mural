"""The three kinds of wallpaper content: GLSL shaders, looping videos and still images.

Every view has pause() / resume() / stop(). Videos never make sound: the pipeline is built
video-only (no audio decoder, no audio output), and is muted with volume 0 as a second guard.
"""
import logging
import os
import time

os.environ.setdefault("PYOPENGL_PLATFORM", "egl")   # GTK4 renders through EGL on Wayland and X11

import logging  # noqa: E402

import OpenGL  # noqa: E402

logging.getLogger("OpenGL.plugins").setLevel(logging.ERROR)   # optional numpy support: not needed

# PyOpenGL checks glGetError after every call by default, which stalls the GPU each frame.
# Shader build errors are still caught through the compile/link status checks below.
OpenGL.ERROR_CHECKING = os.environ.get("MURAL_GL_DEBUG") == "1"
OpenGL.ERROR_LOGGING = False

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gst, Gtk  # noqa: E402

from . import shaderlib  # noqa: E402

log = logging.getLogger(__name__)

# GstPlayFlags (playbin): only VIDEO is set, so no audio or subtitle branch is ever created
PLAY_FLAG_VIDEO = 1 << 0
PLAY_FLAG_AUDIO = 1 << 1
PLAY_FLAG_TEXT = 1 << 2


class ShaderView(Gtk.GLArea):
    """Draws a fragment shader full-screen at a capped frame rate. Animation time stops while paused."""

    def __init__(self, source, palette, fps=30):
        super().__init__(has_depth_buffer=False, has_stencil_buffer=False, auto_render=False,
                         hexpand=True, vexpand=True)
        self.source = source
        self.palette = palette
        self.fps = fps
        self.scale = shaderlib.render_scale(source)   # soft shaders can draw at reduced resolution
        self.error = None
        self._prog = None
        self._vao = None
        self._loc = {}
        self._fbo = None             # (framebuffer, texture, width, height) for reduced-resolution drawing
        self._elapsed = 0.0          # animation seconds before the current run started
        self._run_start = None       # monotonic time of the current run, None while paused
        self._timer = 0
        self._fallback = False
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self.connect("render", self._on_render)
        self.resume()

    # -- time & pacing -------------------------------------------------------
    def anim_time(self):
        if self._run_start is None:
            return self._elapsed
        return self._elapsed + (time.monotonic() - self._run_start)

    def set_fps(self, fps):
        self.fps = fps
        if self._timer:
            self._stop_timer()
            self._start_timer()

    def set_palette(self, palette):
        self.palette = palette
        self.queue_render()

    def _start_timer(self):
        if not self._timer:
            self._timer = GLib.timeout_add(max(1, round(1000 / self.fps)), self._on_timer)

    def _stop_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_timer(self):
        self.queue_render()
        return GLib.SOURCE_CONTINUE

    def pause(self):
        if self._run_start is not None:
            self._elapsed = self.anim_time()
            self._run_start = None
        self._stop_timer()

    def resume(self):
        if self._run_start is None:
            self._run_start = time.monotonic()
        self._start_timer()

    @property
    def paused(self):
        return self._run_start is None

    def stop(self):
        self.pause()

    # -- GL ------------------------------------------------------------------
    def _on_realize(self, area):
        area.make_current()
        if area.get_error() is not None:
            self.error = str(area.get_error())
            log.error("no OpenGL context: %s", self.error)
            return
        from OpenGL import GL
        use_es = area.get_context().get_use_es()
        try:
            self._prog = _link(GL, shaderlib.vertex_source(use_es), shaderlib.fragment_source(self.source, use_es))
        except RuntimeError as e:
            self.error = str(e)
            self._fallback = True
            log.error("shader failed to build:\n%s", e)
            return
        self._vao = GL.glGenVertexArrays(1)
        self._loc = {n: GL.glGetUniformLocation(self._prog, n) for n in shaderlib.UNIFORM_NAMES}

    def _on_unrealize(self, area):
        area.make_current()
        if area.get_error() is not None:
            return
        from OpenGL import GL
        self._drop_fbo(GL)
        if self._vao is not None:
            GL.glDeleteVertexArrays(1, [self._vao])
            self._vao = None
        if self._prog:
            GL.glDeleteProgram(self._prog)
            self._prog = None

    def _on_render(self, area, ctx):
        from OpenGL import GL
        bg = self.palette.background
        if not self._prog:                       # broken shader: a calm solid colour, not garbage
            GL.glClearColor(bg[0], bg[1], bg[2], 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            return True
        _x, _y, w, h = GL.glGetIntegerv(GL.GL_VIEWPORT)   # GTK sets it to the real (scaled) size
        target = GL.glGetIntegerv(GL.GL_DRAW_FRAMEBUFFER_BINDING)   # GTK's framebuffer for this area
        rw, rh = w, h
        if self.scale < 1.0:
            rw, rh = max(1, round(w * self.scale)), max(1, round(h * self.scale))
            fbo = self._ensure_fbo(GL, rw, rh)
            GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, fbo)
            GL.glViewport(0, 0, rw, rh)
        GL.glUseProgram(self._prog)
        GL.glBindVertexArray(self._vao)
        loc, p = self._loc, self.palette
        GL.glUniform1f(loc["iTime"], self.anim_time())
        GL.glUniform3f(loc["iResolution"], float(rw), float(rh), 1.0)
        GL.glUniform3f(loc["uAccent1"], *p.accent1)
        GL.glUniform3f(loc["uAccent2"], *p.accent2)
        GL.glUniform3f(loc["uAccent3"], *p.accent3)
        GL.glUniform3f(loc["uBackground"], *p.background)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        if self.scale < 1.0:                    # smooth upscale into GTK's framebuffer
            GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self._fbo[0])
            GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, int(target))
            GL.glBlitFramebuffer(0, 0, rw, rh, 0, 0, w, h, GL.GL_COLOR_BUFFER_BIT, GL.GL_LINEAR)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, int(target))
            GL.glViewport(0, 0, w, h)
        return True

    def _ensure_fbo(self, GL, w, h):
        if self._fbo and self._fbo[2:] == (w, h):
            return self._fbo[0]
        self._drop_fbo(GL)
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
        self._fbo = (fbo, tex, w, h)
        return fbo

    def _drop_fbo(self, GL):
        if self._fbo:
            GL.glDeleteFramebuffers(1, [self._fbo[0]])
            GL.glDeleteTextures(1, [self._fbo[1]])
            self._fbo = None


def _compile(GL, kind, src):
    sh = GL.glCreateShader(kind)
    GL.glShaderSource(sh, src)
    GL.glCompileShader(sh)
    if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
        msg = GL.glGetShaderInfoLog(sh)
        GL.glDeleteShader(sh)
        raise RuntimeError(msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg))
    return sh


def _link(GL, vsrc, fsrc):
    vs = _compile(GL, GL.GL_VERTEX_SHADER, vsrc)
    try:
        fs = _compile(GL, GL.GL_FRAGMENT_SHADER, fsrc)
    except RuntimeError:
        GL.glDeleteShader(vs)
        raise
    prog = GL.glCreateProgram()
    GL.glAttachShader(prog, vs)
    GL.glAttachShader(prog, fs)
    GL.glLinkProgram(prog)
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        msg = GL.glGetProgramInfoLog(prog)
        GL.glDeleteProgram(prog)
        raise RuntimeError(msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg))
    return prog


class VideoView(Gtk.Picture):
    """A silent, seamlessly looping video, scaled to cover the screen."""

    def __init__(self, path):
        super().__init__(content_fit=Gtk.ContentFit.COVER, can_shrink=True, hexpand=True, vexpand=True)
        if not Gst.is_initialized():
            Gst.init(None)
        self.path = str(path)
        self.error = None
        self._want_playing = True
        self._looping = False
        self.pipeline = Gst.ElementFactory.make("playbin3", None) or Gst.ElementFactory.make("playbin", None)
        sink = Gst.ElementFactory.make("gtk4paintablesink", None)
        if self.pipeline is None or sink is None:
            self.error = "GStreamer's GTK 4 video sink (gst-plugin-gtk4) is not installed"
            log.error(self.error)
            self.pipeline = None
            return
        paintable = sink.get_property("paintable")
        video_sink = sink
        if paintable.get_property("gl-context") is not None:   # zero-copy GL upload when available
            glsink = Gst.ElementFactory.make("glsinkbin", None)
            if glsink is not None:
                glsink.set_property("sink", sink)
                video_sink = glsink
        self.set_paintable(paintable)
        self.pipeline.set_property("uri", Gst.filename_to_uri(os.path.abspath(self.path)))
        self.pipeline.set_property("video-sink", video_sink)
        self.pipeline.set_property("flags", PLAY_FLAG_VIDEO)          # no audio, no subtitles
        fakesink = Gst.ElementFactory.make("fakesink", None)          # never a real audio device
        if fakesink is not None:
            self.pipeline.set_property("audio-sink", fakesink)
        self.pipeline.set_property("mute", True)
        self.pipeline.set_property("volume", 0.0)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_id = bus.connect("message", self._on_message)
        self.pipeline.set_state(Gst.State.PLAYING)

    def _seek_start(self, flush):
        flags = Gst.SeekFlags.SEGMENT | Gst.SeekFlags.ACCURATE
        if flush:
            flags |= Gst.SeekFlags.FLUSH
        self.pipeline.seek_simple(Gst.Format.TIME, flags, 0)

    def _on_message(self, bus, msg):
        t = msg.type
        if t == Gst.MessageType.ASYNC_DONE and not self._looping:
            self._looping = True
            self._seek_start(flush=True)                # segment seeks give a gap-free loop
        elif t == Gst.MessageType.SEGMENT_DONE:
            self._seek_start(flush=False)
        elif t == Gst.MessageType.EOS:                   # fallback if segment looping isn't honoured
            self._seek_start(flush=True)
        elif t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            self.error = err.message
            log.error("video %s: %s (%s)", self.path, err.message, dbg)
            self.pipeline.set_state(Gst.State.NULL)

    def pause(self):
        self._want_playing = False
        if self.pipeline is not None and self.error is None:
            self.pipeline.set_state(Gst.State.PAUSED)

    def resume(self):
        self._want_playing = True
        if self.pipeline is not None and self.error is None:
            self.pipeline.set_state(Gst.State.PLAYING)

    @property
    def paused(self):
        return not self._want_playing

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            bus = self.pipeline.get_bus()
            bus.disconnect(self._bus_id)
            bus.remove_signal_watch()
            self.pipeline = None


class ImageView(Gtk.Picture):
    """A still image, or an animated GIF/WebP played frame by frame, scaled to cover the screen.

    Stills are decoded once (through glycin when GTK has it, so WebP, AVIF, HEIC and JPEG XL work)
    and cost nothing afterwards. Animations only wake up when the next frame is due."""

    def __init__(self, path):
        super().__init__(content_fit=Gtk.ContentFit.COVER, can_shrink=True, hexpand=True, vexpand=True)
        self.error = None
        self.paused = False
        self._anim = None
        self._iter = None
        self._timer = 0
        path = str(path)
        if path.lower().endswith((".gif", ".webp")):
            try:
                from gi.repository import GdkPixbuf
                anim = GdkPixbuf.PixbufAnimation.new_from_file(path)
                if not anim.is_static_image():
                    self._anim = anim
            except GLib.Error:
                pass                                    # not loadable as an animation: try as a still
        if self._anim is not None:
            self._iter = self._anim.get_iter(None)
            self._show_frame()
            self._schedule()
            return
        try:
            self.set_paintable(Gdk.Texture.new_from_filename(path))
        except GLib.Error as e:
            self.error = f"can't open image: {e.message}"

    def _show_frame(self):
        self.set_paintable(Gdk.Texture.new_for_pixbuf(self._iter.get_pixbuf()))

    def _schedule(self):
        delay = self._iter.get_delay_time()
        if delay < 0:                                   # last frame of a non-looping animation
            return
        self._timer = GLib.timeout_add(max(delay, 20), self._tick)

    def _tick(self):
        self._timer = 0
        if self.paused:
            return GLib.SOURCE_REMOVE
        if self._iter.advance(None):
            self._show_frame()
        self._schedule()
        return GLib.SOURCE_REMOVE

    def pause(self):
        self.paused = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def resume(self):
        self.paused = False
        if self._anim is not None and not self._timer:
            self._schedule()

    def stop(self):
        self.pause()
