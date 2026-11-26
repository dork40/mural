import QtQuick
import QtQuick.Controls
import QtMultimedia

Rectangle {
    id: root
    width: 1920
    height: 1200
    color: "#000000"

    readonly property color accent: config.accent || "#cba6f7"
    readonly property color text: "#cdd6f4"
    readonly property color subtext: "#7f849c"
    readonly property color field: "#0d0d0d"
    readonly property color red: "#f38ba8"
    readonly property string fontName: config.font || "JetBrainsMono Nerd Font"
    property int sessionIndex: sessionModel.lastIndex

    Connections {
        target: sddm
        function onLoginFailed() {
            password.text = ""
            status.text = "Wrong password"
            shake.start()
            password.forceActiveFocus()
        }
        function onLoginSucceeded() {
            status.text = ""
        }
    }

    // Background, from theme.conf: kind=shader (a Mural animated wallpaper drawn live on the GPU, so
    // it runs at the screen's own refresh rate), kind=video (a silent looping video) or kind=image.
    readonly property string kind: config.kind || "shader"
    readonly property real renderScale: Math.max(0.25, Math.min(1, Number(config.renderScale || 0.5)))

    Image {
        anchors.fill: parent
        source: Qt.resolvedUrl(root.kind === "image" ? (config.backgroundImage || "background.png") : "background.png")
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        // also the fallback while a video starts, or if the shader or video can't play
        visible: root.kind === "image" || (root.kind === "shader" && wall.status === ShaderEffect.Error)
                 || (root.kind === "video" && video.playbackState !== MediaPlayer.PlayingState)
    }

    Loader {
        id: shaderLoader
        anchors.fill: parent
        active: root.kind === "shader"
        sourceComponent: ShaderEffect {
            id: wall
            fragmentShader: "background.frag.qsb"
            property real iTime: 0
            property size muralRes: Qt.size(width * root.renderScale, height * root.renderScale)
            property color muralAccent1: root.accent
            property color muralAccent2: config.accent2 || "#89b4fa"
            property color muralAccent3: config.accent3 || "#f5c2e7"
            property color muralBackground: config.base || "#11111b"
            // soft picture: draw it at a fraction of the screen's pixels and let the GPU scale it up
            layer.enabled: root.renderScale < 1
            layer.textureSize: Qt.size(width * root.renderScale, height * root.renderScale)
            layer.smooth: true
            FrameAnimation {               // ticks once per displayed frame, at the screen's refresh rate
                running: true
                onTriggered: wall.iTime += frameTime
            }
        }
    }
    readonly property var wall: shaderLoader.item || { status: ShaderEffect.Compiled }

    Video {
        id: video
        anchors.fill: parent
        visible: root.kind === "video"
        source: root.kind === "video" ? Qt.resolvedUrl(config.video || "background.mp4") : ""
        onErrorOccurred: (error, message) => console.warn("Mural login video:", message)
        fillMode: VideoOutput.PreserveAspectCrop
        loops: MediaPlayer.Infinite
        muted: true
        volume: 0
        autoPlay: root.kind === "video"
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#e6000000" }
            GradientStop { position: 0.45; color: "#66000000" }
            GradientStop { position: 1.0; color: "#1a000000" }
        }
    }

    // Clock + login, left side
    Column {
        id: panel
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: root.width * 0.08
        spacing: 14
        width: Math.min(360, root.width * 0.3)

        Row {
            spacing: 14
            Text {
                id: clock
                color: root.text
                font.family: root.fontName
                font.pixelSize: 96
                font.weight: Font.Light
                text: Qt.formatTime(new Date(), "h:mm AP").split(" ")[0]
            }
            Text {
                id: ampm
                anchors.baseline: clock.baseline
                color: root.subtext
                font.family: root.fontName
                font.pixelSize: 28
                text: Qt.formatTime(new Date(), "h:mm AP").split(" ")[1]
            }
        }
        Text {
            id: date
            color: root.accent
            font.family: root.fontName
            font.pixelSize: 18
            text: Qt.formatDate(new Date(), "dddd, d MMMM")
            bottomPadding: 36
        }

        Text {
            color: root.subtext
            font.family: root.fontName
            font.pixelSize: 13
            text: "Welcome back"
        }

        TextField {
            id: user
            width: parent.width
            height: 48
            text: userModel.lastUser
            placeholderText: "Username"
            font.family: root.fontName
            font.pixelSize: 15
            color: root.text
            placeholderTextColor: root.subtext
            leftPadding: 16
            selectByMouse: true
            background: Rectangle {
                radius: 12
                color: root.field
                border.width: 1
                border.color: user.activeFocus ? root.accent : "#26ffffff"
            }
            KeyNavigation.tab: password
            onAccepted: password.forceActiveFocus()
        }

        Item {
            width: parent.width
            height: 48

            TextField {
                id: password
                anchors.fill: parent
                echoMode: TextInput.Password
                passwordCharacter: "•"
                placeholderText: "Password"
                font.family: root.fontName
                font.pixelSize: 15
                color: root.text
                placeholderTextColor: root.subtext
                leftPadding: 16
                rightPadding: 56
                focus: true
                background: Rectangle {
                    radius: 12
                    color: root.field
                    border.width: 1
                    border.color: status.text ? root.red
                                  : password.activeFocus ? root.accent : "#26ffffff"
                }
                KeyNavigation.tab: user
                onAccepted: root.doLogin()
                onTextChanged: if (text) status.text = ""
            }

            // Arrow button inside the password field
            Rectangle {
                anchors.right: parent.right
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                width: 36; height: 36; radius: 9
                color: go.containsMouse ? Qt.lighter(root.accent, 1.1) : root.accent
                Text {
                    anchors.centerIn: parent
                    text: "→"
                    color: "#000000"
                    font.pixelSize: 18
                    font.bold: true
                }
                MouseArea {
                    id: go
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.doLogin()
                }
            }

            SequentialAnimation {
                id: shake
                loops: 2
                NumberAnimation { target: password; property: "anchors.leftMargin"; to: -10; duration: 50 }
                NumberAnimation { target: password; property: "anchors.leftMargin"; to: 10; duration: 50 }
                NumberAnimation { target: password; property: "anchors.leftMargin"; to: 0; duration: 50 }
            }
        }

        Text {
            id: status
            color: root.red
            font.family: root.fontName
            font.pixelSize: 13
            text: keyboard.capsLock ? "Caps Lock is on" : ""
        }
    }

    Timer {
        interval: 1000; running: true; repeat: true
        onTriggered: {
            var t = Qt.formatTime(new Date(), "h:mm AP").split(" ")
            clock.text = t[0]
            ampm.text = t[1]
            date.text = Qt.formatDate(new Date(), "dddd, d MMMM")
        }
    }

    // Session picker, bottom left
    Row {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.margins: 32
        spacing: 8

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "󰍹"
            color: root.subtext
            font.family: root.fontName
            font.pixelSize: 16
        }
        Text {
            id: sessionName
            anchors.verticalCenter: parent.verticalCenter
            color: sessionArea.containsMouse ? root.accent : root.subtext
            font.family: root.fontName
            font.pixelSize: 14
            text: sessionModel.data(sessionModel.index(root.sessionIndex, 0), Qt.UserRole + 4) || "Session"
            MouseArea {
                id: sessionArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.sessionIndex = (root.sessionIndex + 1) % sessionModel.rowCount()
            }
        }
    }

    // Power buttons, bottom right
    Row {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 28
        spacing: 10

        Repeater {
            model: [
                { icon: "󰒲", label: "Suspend",  enabled: sddm.canSuspend,  act: function() { sddm.suspend() } },
                { icon: "󰜉", label: "Reboot",   enabled: sddm.canReboot,   act: function() { sddm.reboot() } },
                { icon: "⏻", label: "Shut down", enabled: sddm.canPowerOff, act: function() { sddm.powerOff() } }
            ]
            delegate: Rectangle {
                required property var modelData
                width: 44; height: 44; radius: 12
                color: btn.containsMouse ? "#1fcba6f7" : "#99000000"
                border.width: 1
                border.color: btn.containsMouse ? root.accent : "#1fffffff"
                Text {
                    anchors.centerIn: parent
                    text: modelData.icon
                    color: btn.containsMouse ? root.accent : root.text
                    font.family: root.fontName
                    font.pixelSize: 18
                }
                MouseArea {
                    id: btn
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: modelData.act()
                }
                ToolTip.visible: btn.containsMouse
                ToolTip.text: modelData.label
                ToolTip.delay: 400
            }
        }
    }

    function doLogin() {
        sddm.login(user.text, password.text, root.sessionIndex)
    }

    Component.onCompleted: password.forceActiveFocus()
}
