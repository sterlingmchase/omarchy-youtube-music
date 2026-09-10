import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "sterling.youtube-music"
  ipcTarget: "sterling.youtube-music"
  manageIpc: false

  readonly property string pluginPath: Quickshell.env("HOME") + "/.config/omarchy/plugins/sterling.youtube-music"
  readonly property string bridgePath: root.pluginPath + "/scripts/browser_bridge.py"
  readonly property string backendPath: root.pluginPath + "/scripts/backend.py"

  property bool connected: false
  property bool active: false
  property bool playing: false
  property bool shuffled: false
  property string repeatMode: "off"
  property string trackTitle: "YouTube Music"
  property string artistName: ""
  property string albumName: ""
  property string artUrl: ""
  property real position: 0
  property real duration: 0
  property var resultItems: []
  property string resultHeading: ""
  property string resultError: ""
  property bool resultsLoading: false
  property bool trackLoading: false
  property bool playerWarmed: false
  property bool authWindowOpened: false
  property bool reconnecting: false
  property string searchType: "songs"

  readonly property string fullBarText: root.active
    ? ("  " + root.trackTitle + (root.artistName ? " — " + root.artistName : "")) : ""
  readonly property string barText: barTextMetrics.elidedText
  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property color dimForeground: Qt.darker(contentForeground, 1.5)
  readonly property color subtleFill: Style.normalFillFor(contentForeground, Color.accent)
  readonly property color subtleBorder: Style.normalBorderFor(contentForeground, Color.accent)
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function openSignIn() {
    if (actionProc.running) return
    root.authWindowOpened = true
    actionProc.command = ["python3", root.bridgePath, "focus"]
    actionProc.running = true
  }
  function finishSignIn() {
    if (actionProc.running) return
    actionProc.command = ["python3", root.bridgePath, "capture"]
    actionProc.running = true
  }
  function disconnectAccount() {
    if (actionProc.running || resultsProc.running || controlProc.running) return
    root.resultItems = []; root.resultHeading = ""
    actionProc.command = ["python3", root.backendPath, "disconnect"]
    actionProc.running = true
  }
  function runResults(action, argument, heading, option) {
    if (resultsProc.running || !root.connected) return
    root.resultItems = []; root.resultHeading = heading; root.resultError = ""; root.resultsLoading = true
    var command = ["python3", root.backendPath, action]
    if (argument) command.push(String(argument))
    if (option) command.push(String(option))
    resultsProc.command = command; resultsProc.running = true
  }
  function runControl(action, argument) {
    if (controlProc.running || actionProc.running) return
    var command = ["python3", root.backendPath, action]
    if (argument !== undefined && argument !== "") command.push(String(argument))
    root.resultError = ""
    controlProc.actionName = action
    if (action === "play") root.trackLoading = true
    controlProc.command = command; controlProc.running = true
  }
  function openSearch() {
    var query = String(searchField.text || "").trim()
    if (query === "") { searchField.forceActiveFocus(); return }
    root.runResults("search", query, "Search results", root.searchType)
  }
  function chooseResult(item) {
    if (!item) return
    if (item.kind === "song") {
      root.trackTitle = String(item.title || "YouTube Music")
      root.artistName = String(item.subtitle || "")
      root.artUrl = String(item.thumbnail || "")
      root.runControl("play", item.id)
    } else root.runResults("browse", item.id, String(item.title || "Tracks"))
  }
  function refreshStatus(checkAuth) {
    if (statusProc.running) return
    statusProc.command = ["python3", root.backendPath, "status"]
    if (checkAuth) statusProc.command = ["python3", root.backendPath, "status", "auth"]
    statusProc.running = true
  }
  function applyStatus(value) {
    if (value.connected !== undefined && !root.reconnecting) {
      root.connected = Boolean(value.connected)
      if (root.connected && !root.playerWarmed && !root.active) {
        root.playerWarmed = true
        warmProc.running = true
      }
    }
    root.active = Boolean(value.active)
    root.playing = Boolean(value.playing); root.shuffled = Boolean(value.shuffle)
    root.repeatMode = String(value.repeat || "off")
    root.position = Number(value.position || 0); root.duration = Number(value.duration || 0)
    if (value.title) root.trackTitle = String(value.title)
    if (value.artist) root.artistName = String(value.artist)
    if (value.album) root.albumName = String(value.album)
  }
  function formatTime(seconds) {
    if (!seconds || seconds <= 0) return "0:00"
    var total = Math.floor(seconds), minutes = Math.floor(total / 60), remainder = total % 60
    return minutes + ":" + (remainder < 10 ? "0" : "") + remainder
  }

  onOpenedChanged: if (opened) { root.refreshStatus(true); Qt.callLater(function() { keyCatcher.forceActiveFocus() }) }
  Component.onCompleted: root.refreshStatus(true)

  Timer { interval: root.opened ? 1000 : 5000; running: root.opened || root.active; repeat: true; onTriggered: root.refreshStatus() }
  Timer { id: statusDelay; interval: 350; repeat: false; onTriggered: root.refreshStatus() }

  Process {
    id: statusProc
    command: ["python3", root.backendPath, "status"]
    running: false
    stdout: StdioCollector {
      id: statusStdout; waitForEnd: true
      onStreamFinished: {
        try { root.applyStatus(JSON.parse(text)) }
        catch (error) { root.resultError = "Could not read connection status." }
      }
    }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim()) root.resultError = text.trim() }
  }
  Process {
    id: resultsProc
    running: false
    stdout: StdioCollector { id: resultsStdout; waitForEnd: true }
    stderr: StdioCollector { id: resultsStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root.resultsLoading = false
      try {
        var response = JSON.parse(String(resultsStdout.text || "{}"))
        if (response.error) throw new Error(String(response.error))
        root.resultItems = response.items || []
        if (root.resultItems.length === 0) root.resultError = "Nothing found"
      } catch (error) {
        var detail = String(resultsStderr.text || error.message || "Request failed").trim()
        root.resultError = detail !== "" ? detail : "Request failed"
      }
    }
  }
  Process {
    id: controlProc
    property string actionName: ""
    stdout: StdioCollector { id: controlStdout; waitForEnd: true }
    onExited: function(exitCode) {
      if (actionName === "play") root.trackLoading = false
      try {
        var response = JSON.parse(String(controlStdout.text || "{}"))
        if (response.error) root.resultError = String(response.error)
        else if (exitCode !== 0) root.resultError = "Playback command failed. Please try again."
      } catch (error) { root.resultError = "Player did not respond. Please try again." }
      statusDelay.restart()
    }
  }
  Process {
    id: warmProc
    command: ["python3", root.backendPath, "warm"]
    running: false
  }
  Process {
    id: actionProc
    running: false
    stdout: StdioCollector { id: actionStdout; waitForEnd: true }
    stderr: StdioCollector { id: actionStderr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.resultError = ""; root.authWindowOpened = false
        if (command[2] === "capture" || command[2] === "disconnect") root.reconnecting = false
        if (command[2] === "capture") root.connected = true
        if (command[2] === "disconnect") root.connected = false
        root.refreshStatus(true)
      }
      else {
        try { root.resultError = JSON.parse(String(actionStdout.text)).error || "Account action failed" }
        catch (error) { root.resultError = String(actionStderr.text || "Account action failed. Please try again.").trim() }
      }
    }
  }

  TextMetrics {
    id: barTextMetrics; text: root.fullBarText; font.family: root.contentFontFamily
    font.pixelSize: Style.font.caption; elide: Text.ElideRight; elideWidth: Style.space(190)
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function playPause(): string { root.runControl("toggle"); return root.active ? "ok" : "idle" }
    function next(): string { root.runControl("next"); return root.active ? "ok" : "idle" }
    function previous(): string { root.runControl("previous"); return root.active ? "ok" : "idle" }
    function launch(): string { root.openSignIn(); return "ok" }
    function playlists(): string { root.runResults("library", "", "Your playlists"); return "ok" }
    function status(): string { return root.active ? root.trackTitle : "idle" }
    function connectionStatus(): string { return JSON.stringify({ connected: root.connected, reconnecting: root.reconnecting, busy: actionProc.running, error: root.resultError }) }
  }

  BarIconButton {
    id: button; anchors.fill: parent; bar: root.bar; text: root.barText; labelVisible: true
    fixedWidth: -1; fontSize: Style.font.caption; active: root.playing; useActiveColor: true
    foreground: root.barForeground; tooltipText: root.active ? root.trackTitle : "YouTube Music"
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton && root.active) root.runControl("toggle")
      else if (buttonCode === Qt.RightButton && root.active) root.runControl("next")
      else if (buttonCode === Qt.LeftButton) root.toggle()
    }
  }

  KeyboardPanel {
    id: panel; anchorItem: button; owner: root; bar: root.bar; open: root.opened
    centerOnBar: true; focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher; anchors.fill: parent
      blocked: searchField.activeFocus || searchTypeDropdown.popupOpen
      onMoveRequested: function(dx, dy) {
        if (dx > 0 && root.active) root.runControl("next")
        else if (dx < 0 && root.active) root.runControl("previous")
      }
      onActivateRequested: if (root.active) root.runControl("toggle")
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "/") searchField.forceActiveFocus()
        else if (text === " " && root.active) root.runControl("toggle")
        else if (text === "n" && root.active) root.runControl("next")
        else if (text === "p" && root.active) root.runControl("previous")
        else if (text === "s" && root.active) root.runControl("shuffle")
        else if (text === "r" && root.active) root.runControl("repeat")
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
      Column {
        id: contentColumn; width: parent.width; spacing: Style.space(11)
        PanelHero {
          width: parent.width; title: root.active ? root.trackTitle : "YouTube Music"
          meta: root.trackLoading ? "Starting playback…"
            : root.active ? (root.artistName || root.albumName || "Now playing")
            : (root.connected ? "Ready in the plugin" : "One-time browser sign-in")
          foreground: root.contentForeground; fontFamily: root.contentFontFamily
          iconComponent: Component {
            Rectangle {
              radius: Style.cornerRadius; color: root.subtleFill; clip: true
              Text {
                anchors.centerIn: parent; text: ""; color: Color.accent
                font.family: root.contentFontFamily; font.pixelSize: Style.font.display
              }
              Image {
                anchors.fill: parent; visible: root.artUrl !== "" && status !== Image.Error
                source: root.artUrl; fillMode: Image.PreserveAspectCrop; asynchronous: true; cache: true
              }
            }
          }
        }
        PanelSeparator { foreground: root.contentForeground }

        Column {
          visible: !root.connected; width: parent.width; spacing: Style.space(7)
          Text {
            width: parent.width
            text: "Open sign-in, then return here and finish connecting. Your session is saved in your desktop keyring. Google may occasionally require you to reconnect."
            color: root.dimForeground; font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall; wrapMode: Text.WordWrap
          }
          Row {
            width: parent.width; spacing: Style.space(8)
            Button {
              width: (parent.width - parent.spacing) / 2; text: "1. Open sign-in"; bordered: true
              foreground: root.contentForeground; fontFamily: root.contentFontFamily; onClicked: root.openSignIn()
            }
            Button {
              width: (parent.width - parent.spacing) / 2
              text: actionProc.running ? "Saving…" : "2. Finish sign-in"; bordered: true
              foreground: root.contentForeground; fontFamily: root.contentFontFamily
              enabled: !actionProc.running; onClicked: root.finishSignIn()
            }
          }
        }

        Row {
          visible: root.connected; width: parent.width; spacing: Style.space(8)
          Dropdown {
            id: searchTypeDropdown; width: Style.space(92); showLabel: false; value: root.searchType
            options: [
              { value: "songs", label: "Songs" },
              { value: "albums", label: "Albums" },
              { value: "artists", label: "Artists" }
            ]
            foreground: root.contentForeground; fontFamily: root.contentFontFamily
            onChanged: function(nextValue) { root.searchType = nextValue }
          }
          TextField {
            id: searchField
            width: parent.width - searchTypeDropdown.width - searchButton.width - parent.spacing * 2
            placeholderText: "Search YouTube Music"; foreground: root.contentForeground; accent: Color.accent
            onAccepted: root.openSearch()
            Keys.onEscapePressed: { focus = false; keyCatcher.forceActiveFocus() }
          }
          Button {
            id: searchButton; text: root.resultsLoading ? "Searching…" : "Search"; bordered: true
            enabled: !root.resultsLoading
            foreground: root.contentForeground; fontFamily: root.contentFontFamily; onClicked: root.openSearch()
          }
        }

        Row {
          visible: root.connected; width: parent.width; spacing: Style.space(8)
          Button {
            width: (parent.width - parent.spacing) / 2
            text: root.resultsLoading ? "Loading…" : "Your playlists"; bordered: true
            foreground: root.contentForeground; fontFamily: root.contentFontFamily
            enabled: !root.resultsLoading; onClicked: root.runResults("library", "", "Your playlists")
          }
          Button {
            width: (parent.width - parent.spacing) / 2; text: "Forget login"; bordered: true
            enabled: !actionProc.running && !resultsProc.running && !controlProc.running
            foreground: root.contentForeground; fontFamily: root.contentFontFamily
            onClicked: root.disconnectAccount()
          }
        }
        Button {
          visible: root.connected
          text: "Reconnect account"
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
          onClicked: { root.reconnecting = true; root.connected = false; root.openSignIn() }
        }

        Text {
          visible: root.resultError !== ""; width: parent.width; text: root.resultError
          color: root.dimForeground; font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall; wrapMode: Text.WordWrap
        }
        Column {
          visible: root.resultsLoading || root.resultItems.length > 0
          width: parent.width; spacing: Style.space(6)
          Text {
            width: parent.width; text: root.resultsLoading ? "Loading…" : root.resultHeading
            color: root.contentForeground; font.family: root.contentFontFamily
            font.pixelSize: Style.font.body; font.bold: true
          }
          Rectangle {
            width: parent.width
            height: Style.space(180)
            color: "transparent"
            radius: Style.cornerRadius
            border.width: 1
            border.color: root.subtleBorder
            clip: true

            ListView {
              id: resultsList
              anchors.fill: parent
              anchors.margins: Style.space(6)
              clip: true
              spacing: Style.space(6)
              boundsBehavior: Flickable.StopAtBounds
              flickableDirection: Flickable.VerticalFlick
              model: root.resultItems
              onModelChanged: positionViewAtBeginning()
              Controls.ScrollBar.vertical: Controls.ScrollBar {
                policy: Controls.ScrollBar.AsNeeded
              }
              delegate: Button {
                required property var modelData
                width: resultsList.width - Style.space(12)
                text: String(modelData.title || "Untitled")
                  + (modelData.subtitle ? "  —  " + String(modelData.subtitle) : "")
                bordered: true; foreground: root.contentForeground; fontFamily: root.contentFontFamily
                onClicked: root.chooseResult(modelData)
              }
            }
          }
        }

        Column {
          visible: root.active; width: parent.width; spacing: Style.space(3)
          PanelSlider {
            id: progressSlider; width: parent.width; bar: root.bar; minimum: 0
            maximum: Math.max(1, root.duration); value: root.position; step: 5
            trackHeight: Math.max(3, Style.space(3)); knobSize: Style.space(10)
            onReleased: function(nextPosition) { root.runControl("seek", nextPosition) }
          }
          RowLayout {
            width: parent.width
            Text {
              text: root.formatTime(progressSlider.dragging ? progressSlider.liveValue : root.position)
              color: root.dimForeground; font.family: root.contentFontFamily; font.pixelSize: Style.font.caption
            }
            Item { Layout.fillWidth: true }
            Text {
              text: root.formatTime(root.duration); color: root.dimForeground
              font.family: root.contentFontFamily; font.pixelSize: Style.font.caption
            }
          }
        }

        Item {
          visible: root.active || root.trackLoading; width: parent.width; height: Style.space(38)
          Row {
            anchors.centerIn: parent; spacing: Style.space(11)
            PanelActionButton {
              iconText: ""; tooltipText: "Previous track"; size: Style.space(34)
              foreground: root.contentForeground; hoverColor: Color.accent
              fontFamily: root.contentFontFamily; bordered: false; enabled: root.active && !root.trackLoading
              onClicked: root.runControl("previous")
            }
            PanelActionButton {
              iconText: root.trackLoading ? "" : (root.playing ? "" : "")
              tooltipText: root.trackLoading ? "Starting playback" : (root.playing ? "Pause" : "Play")
              size: Style.space(34); fontSize: Style.font.iconLarge; foreground: Color.accent
              hoverColor: Color.accent; fontFamily: root.contentFontFamily; bordered: false
              enabled: root.active && !root.trackLoading
              onClicked: root.runControl("toggle")
            }
            PanelActionButton {
              iconText: ""; tooltipText: "Next track"; size: Style.space(34)
              foreground: root.contentForeground; hoverColor: Color.accent
              fontFamily: root.contentFontFamily; bordered: false; enabled: root.active && !root.trackLoading
              onClicked: root.runControl("next")
            }
            PanelActionButton {
              iconText: ""; tooltipText: root.shuffled ? "Shuffle on" : "Shuffle"; size: Style.space(34)
              foreground: root.shuffled ? Color.accent : root.contentForeground
              hoverColor: Color.accent; fontFamily: root.contentFontFamily; bordered: false
              enabled: root.active && !root.trackLoading
              onClicked: root.runControl("shuffle")
            }
            PanelActionButton {
              iconText: root.repeatMode === "one" ? "" : ""
              tooltipText: "Repeat " + root.repeatMode; size: Style.space(34)
              foreground: root.repeatMode !== "off" ? Color.accent : root.contentForeground
              hoverColor: Color.accent; fontFamily: root.contentFontFamily; bordered: false
              enabled: root.active && !root.trackLoading
              onClicked: root.runControl("repeat")
            }
          }
        }

        Text {
          visible: root.connected; width: parent.width
          text: "/ search  •  Space play/pause  •  s shuffle  •  r repeat  •  Esc close"
          color: root.dimForeground; font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        }
      }
      }
    }
  }
}
