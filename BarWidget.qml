import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "sterling.youtube-music"

  readonly property var panelItem: panelLoader.item
  readonly property bool opened: panelItem ? panelItem.opened === true : false
  readonly property bool popoutSwitchClosing: panelItem
    ? panelItem.popoutSwitchClosing === true : false

  function injectPanel() {
    if (!panelItem) return
    panelItem.bar = root.bar
    panelItem.settings = root.settings
    panelItem.anchorItem = button
    panelItem.hostWidget = root
  }

  function open() { if (panelItem) panelItem.open() }
  function close() { if (panelItem) panelItem.close() }
  function toggle() { if (panelItem) panelItem.toggle() }
  function closeForPopoutSwitch() { if (panelItem) panelItem.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: root.moduleName
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function playPause(): string {
      if (!root.panelItem || !root.panelItem.active) return "idle"
      root.panelItem.runControl("toggle")
      return "ok"
    }
    function next(): string {
      if (!root.panelItem || !root.panelItem.active) return "idle"
      root.panelItem.runControl("next")
      return "ok"
    }
    function previous(): string {
      if (!root.panelItem || !root.panelItem.active) return "idle"
      root.panelItem.runControl("previous")
      return "ok"
    }
    function launch(): string {
      if (root.panelItem) root.panelItem.openSignIn()
      return "ok"
    }
    function playlists(): string {
      if (root.panelItem) root.panelItem.runResults("library", "", "Your playlists")
      return "ok"
    }
    function status(): string {
      return root.panelItem && root.panelItem.active ? root.panelItem.trackTitle : "idle"
    }
    function connectionStatus(): string {
      return root.panelItem ? root.panelItem.connectionStatus() : "{\"connected\":false}"
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.panelItem ? root.panelItem.barText : ""
    labelVisible: true
    fixedWidth: -1
    fontSize: Style.font.caption
    active: root.panelItem ? root.panelItem.playing : false
    useActiveColor: true
    foreground: root.bar ? root.bar.barForeground : Color.foreground
    tooltipText: root.panelItem && root.panelItem.active
      ? root.panelItem.trackTitle : "YouTube Music"

    onPressed: function(buttonCode) {
      if (!root.panelItem) return
      if (buttonCode === Qt.MiddleButton && root.panelItem.active)
        root.panelItem.runControl("toggle")
      else if (buttonCode === Qt.RightButton && root.panelItem.active)
        root.panelItem.runControl("next")
      else if (buttonCode === Qt.LeftButton)
        root.toggle()
    }
  }
}
