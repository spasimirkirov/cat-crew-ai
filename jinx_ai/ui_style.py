"""Dark acrylic theme — Apple-like chrome, cyan/pink sparks."""

APP_QSS = """
QMainWindow {
    background-color: #090b12;
    color: #f5f5f7;
    font-family: 'Segoe UI Variable', 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}
QWidget#root, QStackedWidget, QWidget#page {
    background-color: #090b12;
    color: #f5f5f7;
}
QStackedWidget > QWidget {
    background-color: #090b12;
}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
    background-color: #090b12;
    border: none;
}

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 3px 2px 3px 0;
    border: none;
}
QScrollBar::handle:vertical {
    background: rgba(142, 142, 147, 0.38);
    min-height: 28px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(34, 211, 238, 0.55);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    width: 0;
    background: none;
    border: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0 3px 2px 3px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: rgba(142, 142, 147, 0.38);
    min-width: 28px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(34, 211, 238, 0.55);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    height: 0;
    width: 0;
    background: none;
    border: none;
}

QFrame#sidebar {
    background-color: #0c0e16;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}
QLabel {
    background: transparent;
    color: #f5f5f7;
}
QLabel#brand {
    color: #f472b6;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 2px;
}
QLabel#h1 {
    font-size: 22px;
    font-weight: 650;
    letter-spacing: -0.4px;
    color: #f5f5f7;
}
QLabel#muted {
    color: #8e8e93;
    font-size: 12px;
    font-weight: 400;
}
QLabel#switchTitle {
    font-size: 14px;
    font-weight: 600;
    color: #f5f5f7;
}
QLabel#switchState {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
    color: #8e8e93;
    padding: 4px 0;
}
QLabel#modelName {
    font-size: 12.5px;
    font-weight: 600;
    color: #f5f5f7;
}
QLabel#modelName a {
    color: #22d3ee;
    text-decoration: none;
}
QLabel#modelName a:hover {
    color: #67e8f9;
    text-decoration: underline;
}
QLabel#hostTitle {
    font-size: 15px;
    font-weight: 650;
    letter-spacing: -0.2px;
    color: #f5f5f7;
}
QPushButton#nav {
    background: transparent;
    color: #c7c7cc;
    text-align: left;
    padding: 10px 12px;
    border: 1px solid transparent;
    border-radius: 10px;
    font-weight: 600;
    font-size: 13px;
}
QPushButton#nav:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #fff;
}
QPushButton#nav:checked {
    background: rgba(34, 211, 238, 0.12);
    border: 1px solid rgba(34, 211, 238, 0.28);
    color: #fff;
}
QGroupBox {
    background: rgba(18, 21, 31, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
    margin-top: 14px;
    padding: 16px 14px 12px 14px;
    font-weight: 600;
    color: #e8e8ed;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: #8e8e93;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QTextBrowser {
    background-color: #0e1118;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 12px;
    padding: 10px;
    color: #e8e8ed;
    font-size: 13px;
}
QLineEdit {
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    padding: 9px 12px;
    color: #f5f5f7;
    selection-background-color: rgba(34, 211, 238, 0.35);
}
QLineEdit:focus { border: 1px solid rgba(34, 211, 238, 0.55); }
QPushButton {
    background-color: rgba(34, 211, 238, 0.16);
    color: #ecfeff;
    border: 1px solid rgba(34, 211, 238, 0.28);
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 650;
}
QPushButton:hover { background-color: rgba(34, 211, 238, 0.26); }
QPushButton:pressed { background-color: rgba(34, 211, 238, 0.12); }
QPushButton:disabled {
    background-color: rgba(255, 255, 255, 0.04);
    color: #636366;
    border-color: rgba(255, 255, 255, 0.05);
}
QRadioButton {
    spacing: 10px;
    padding: 6px 8px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    font-weight: 500;
    color: #e8e8ed;
}
QRadioButton:hover { background: rgba(255, 255, 255, 0.04); }
QRadioButton:checked {
    border: 1px solid rgba(34, 211, 238, 0.28);
    background: rgba(34, 211, 238, 0.08);
    color: #fff;
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 2px solid #8e8e93;
    background: #090b12;
}
QRadioButton::indicator:checked {
    background: #22d3ee;
    border: 2px solid #a5f3fc;
}
QSpinBox {
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px;
    padding: 6px 10px;
    min-width: 84px;
    min-height: 28px;
    color: #f5f5f7;
    font-weight: 650;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 18px;
    background: rgba(255, 255, 255, 0.06);
    border: none;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: rgba(34, 211, 238, 0.28); }
QFrame#switchRow {
    background: rgba(18, 21, 31, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
}
QFrame#switchRow:hover {
    border-color: rgba(244, 114, 182, 0.35);
    background: rgba(244, 114, 182, 0.06);
}
QComboBox {
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px;
    padding: 6px 10px;
    min-height: 28px;
    color: #f5f5f7;
}
QComboBox#toolbarCombo {
    padding: 2px 8px;
    min-height: 24px;
    max-height: 28px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
}
QComboBox::drop-down { border: none; width: 28px; }
QComboBox#toolbarCombo::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #12151f;
    color: #e8e8ed;
    border: 1px solid rgba(255, 255, 255, 0.08);
    selection-background-color: rgba(34, 211, 238, 0.28);
}
QSlider::groove:horizontal {
    height: 4px;
    background: rgba(255, 255, 255, 0.10);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: #f5f5f7;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #22d3ee, stop:1 #f472b6);
    border-radius: 2px;
}
QPushButton#secondary {
    background-color: rgba(255, 255, 255, 0.06);
    color: #e8e8ed;
    border: 1px solid rgba(255, 255, 255, 0.08);
    font-weight: 600;
    padding: 7px 12px;
}
QPushButton#secondary:hover { background-color: rgba(255, 255, 255, 0.10); }
QPushButton#secondary:pressed { background-color: rgba(255, 255, 255, 0.04); }
QPushButton#chipBtn, QPushButton#chipPrimary {
    border-radius: 8px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 650;
    min-height: 24px;
    max-height: 26px;
}
QPushButton#chipBtn {
    background: rgba(255, 255, 255, 0.06);
    color: #e8e8ed;
    border: 1px solid rgba(255, 255, 255, 0.08);
}
QPushButton#chipBtn:hover { background: rgba(255, 255, 255, 0.11); }
QPushButton#chipPrimary {
    background: rgba(34, 211, 238, 0.16);
    color: #ecfeff;
    border: 1px solid rgba(34, 211, 238, 0.32);
}
QPushButton#chipDanger {
    background: rgba(244, 114, 182, 0.10);
    color: #fecdd3;
    border: 1px solid rgba(244, 114, 182, 0.28);
    border-radius: 8px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 650;
    min-height: 24px;
    max-height: 26px;
}
QPushButton#chipDanger:hover {
    background: rgba(255, 95, 87, 0.28);
    border-color: rgba(255, 95, 87, 0.55);
    color: #fff;
}
QProgressBar#pullBar {
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    min-height: 10px;
    max-height: 10px;
    text-align: center;
}
QProgressBar#pullBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #22d3ee, stop:1 #f472b6);
    border-radius: 5px;
}
QFrame#downloadFlyout QProgressBar#pullBar {
    min-height: 6px;
    max-height: 6px;
    border-radius: 3px;
}
QFrame#downloadFlyout QLabel#sectionLabel {
    padding: 4px 0 1px 0;
}
QFrame#pullStrip {
    background: rgba(34, 211, 238, 0.07);
    border: 1px solid rgba(34, 211, 238, 0.22);
    border-radius: 10px;
}
QPushButton#chipPrimary:hover { background: rgba(34, 211, 238, 0.26); }
QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 10px;
    background: #0e1118;
}
QTabBar::tab {
    background: transparent;
    color: #8e8e93;
    padding: 8px 14px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QTabBar::tab:selected {
    background: rgba(34, 211, 238, 0.10);
    color: #f5f5f7;
}
QTreeView, QListWidget, QTableWidget, QPlainTextEdit {
    background-color: #0e1118;
    color: #e8e8ed;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 10px;
    font-size: 12px;
}
QHeaderView::section {
    background: #161922;
    color: #c7c7cc;
    padding: 4px 8px;
    border: none;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}
QTableWidget::item:selected, QListWidget::item:selected, QTreeView::item:selected {
    background: rgba(34, 211, 238, 0.22);
    color: #fff;
}
QSplitter::handle {
    background: rgba(255, 255, 255, 0.05);
    width: 3px;
    height: 3px;
}
QMenu {
    background: #161922;
    color: #e8e8ed;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px;
    padding: 4px;
}
QMenu::item { padding: 6px 12px; border-radius: 6px; }
QMenu::item:selected { background: rgba(34, 211, 238, 0.22); }
QMenu::separator {
    height: 1px;
    background: rgba(255, 255, 255, 0.08);
    margin: 4px 10px;
}
QToolTip {
    background-color: #161922;
    color: #f5f5f7;
    border: 1px solid rgba(34, 211, 238, 0.35);
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}
QCheckBox { color: #e8e8ed; spacing: 8px; }
QFrame#activityBar {
    background-color: #08090f;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}
QPushButton#activity {
    background: transparent;
    color: #8e8e93;
    font-weight: 700;
    font-size: 12px;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 0;
}
QPushButton#activity:hover { background: rgba(255, 255, 255, 0.06); color: #fff; }
QPushButton#activity:checked {
    background: rgba(34, 211, 238, 0.12);
    border: 1px solid rgba(34, 211, 238, 0.28);
    color: #fff;
}
QPushButton#activity[role="quit"]:hover { background: rgba(255, 95, 87, 0.18); }
QToolButton#iconBtn, QToolButton#dangerIcon,
QPushButton#iconBtn, QPushButton#dangerIcon {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 0;
    color: #e8e8ed;
}
QToolButton#iconBtn:hover, QPushButton#iconBtn:hover {
    background: rgba(255, 255, 255, 0.10);
    border-color: rgba(34, 211, 238, 0.35);
}
QPushButton#iconBtn[active="true"] {
    background: rgba(34, 211, 238, 0.16);
    border-color: rgba(34, 211, 238, 0.45);
}
QPushButton#iconToggle {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 0;
}
QPushButton#iconToggle:hover {
    background: rgba(255, 255, 255, 0.10);
}
QPushButton#iconToggle[on="true"] {
    background: rgba(40, 200, 64, 0.14);
    border-color: rgba(40, 200, 64, 0.45);
}
QPushButton#iconToggle[on="false"] {
    background: rgba(255, 95, 87, 0.12);
    border-color: rgba(255, 95, 87, 0.40);
}
QPushButton#iconToggle:disabled {
    background: rgba(255, 255, 255, 0.03);
    border-color: rgba(255, 255, 255, 0.06);
}
QFrame#downloadFlyout {
    background: #12151f;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 12px;
}
QToolButton#iconBtn:pressed, QPushButton#iconBtn:pressed { background: rgba(34, 211, 238, 0.16); }
QToolButton#dangerIcon:hover, QPushButton#dangerIcon:hover {
    background: rgba(255, 95, 87, 0.18);
    border-color: rgba(255, 95, 87, 0.55);
}
QWidget#titleBar {
    background: #0c0e16;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
QLabel#titleBrand {
    color: #f5f5f7;
    font-size: 13px;
    font-weight: 650;
    letter-spacing: -0.1px;
}
QLabel#titleMode {
    color: #a5f3fc;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 9px;
    border: 1px solid rgba(34, 211, 238, 0.28);
    border-radius: 999px;
    background: rgba(34, 211, 238, 0.10);
}
QLabel#bubbleTag {
    color: #a5f3fc;
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.2px;
}
QFrame#userBubble QLabel#bubbleTag { color: #fbcfe8; }
QLabel#bubbleTime {
    color: #8e8e93;
    font-size: 10px;
    padding-top: 2px;
}
QScrollArea#chatTranscript, QWidget#chatInner {
    background: #090b12;
    border: none;
}
QFrame#userBubble {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(34, 211, 238, 0.22), stop:1 rgba(244, 114, 182, 0.22));
    border: 1px solid rgba(244, 114, 182, 0.28);
    border-radius: 16px 16px 4px 16px;
}
QFrame#jinxBubble {
    background: rgba(18, 21, 31, 0.96);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px 16px 16px 4px;
}
QFrame#sysBubble {
    background: rgba(255, 95, 87, 0.10);
    border: 1px solid rgba(255, 95, 87, 0.28);
    border-radius: 12px;
}
QFrame#activityCard {
    background: transparent;
    border: none;
}
QLabel#activityTitle {
    color: #8e8e93;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#activityChevron {
    background: transparent;
    border: none;
    padding: 0;
}
QPushButton#activityChevron:hover {
    background: rgba(255, 255, 255, 0.06);
    border-radius: 6px;
}
QPushButton#activityStep {
    text-align: left;
    color: #8e8e93;
    border: none;
    background: transparent;
    padding: 2px 0;
    font-size: 12px;
}
QPushButton#activityStep:hover {
    color: #e8e8ed;
}
QFrame#approvalBar {
    background: rgba(18, 21, 31, 0.96);
    border: 1px solid rgba(244, 114, 182, 0.35);
    border-left: 2px solid #f472b6;
    border-radius: 14px;
}
QFrame#askUserBar {
    background: rgba(18, 21, 31, 0.96);
    border: 1px solid rgba(34, 211, 238, 0.35);
    border-left: 2px solid #22d3ee;
    border-radius: 14px;
}
QLabel#bubblePhoto {
    border-radius: 10px;
    margin-top: 6px;
}
QWidget#bubblePhotos {
    background: transparent;
}
QLabel#loadedBadge, QLabel#pill[kind="loaded"] {
    color: #34d399;
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.4px;
    padding: 2px 7px;
    border: 1px solid rgba(52, 211, 153, 0.35);
    border-radius: 999px;
    background: rgba(52, 211, 153, 0.10);
}
QLabel#capChip, QLabel#pill {
    background: rgba(244, 114, 182, 0.10);
    color: #fbcfe8;
    border: 1px solid rgba(244, 114, 182, 0.22);
    border-radius: 999px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 650;
}
QLabel#pill[kind="chat"] {
    background: rgba(34, 211, 238, 0.12);
    color: #a5f3fc;
    border-color: rgba(34, 211, 238, 0.30);
}
QFrame#hostCard {
    background: rgba(18, 21, 31, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 16px;
}
QFrame#modelRow {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 10px;
}
QFrame#modelRow:hover {
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(34, 211, 238, 0.18);
}
QLabel#bubbleBody {
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
    color: #f5f5f7;
    font-size: 10pt;
    selection-background-color: rgba(34, 211, 238, 0.35);
}
QLabel#bubbleBody a { color: #67e8f9; }

QLabel#sectionLabel {
    color: #8e8e93;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.7px;
    padding: 12px 0 4px 0;
}
QFrame#settingsNav {
    background: #0c0e16;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}
QPushButton#settingsNavBtn {
    background: transparent;
    color: #c7c7cc;
    text-align: left;
    padding: 8px 10px;
    border: 1px solid transparent;
    border-radius: 8px;
    font-weight: 600;
    font-size: 13px;
}
QPushButton#settingsNavBtn:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #fff;
}
QPushButton#settingsNavBtn:checked {
    background: rgba(34, 211, 238, 0.12);
    border: 1px solid rgba(34, 211, 238, 0.28);
    color: #fff;
}
QFrame#settingsCard {
    background: rgba(18, 21, 31, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
}
QFrame#botRow {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
}
QFrame#botRow:hover {
    background: rgba(255, 255, 255, 0.04);
}
QFrame#botRow[selected="true"] {
    background: rgba(34, 211, 238, 0.12);
    border: 1px solid rgba(34, 211, 238, 0.28);
}
QLabel#botName {
    font-weight: 650;
    font-size: 13px;
    color: #f5f5f7;
}
QLabel#botAvatar,
QLabel#memberPreview {
    background: transparent;
    border: none;
}
QWidget#circleCrop {
    background: #090b12;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
}
QLabel#botPresence {
    background: #636366;
    border-radius: 4px;
}
QLabel#botPresence[state="idle"] { background: #636366; }
QLabel#botPresence[state="thinking"] { background: #a78bfa; }
QLabel#botPresence[state="working"] { background: #22d3ee; }
QLabel#botPresence[state="waiting"] { background: #febc2e; }
QLabel#botPresence[state="blocked"] { background: #ff5f57; }
QLabel#botPresence[state="done"] { background: #28c840; }
QFrame#gitFileRow {
    background: transparent;
    border: none;
    border-radius: 4px;
}
QFrame#gitFileRow:hover {
    background: rgba(255, 255, 255, 0.04);
}
QFrame#gitSectionHead {
    background: transparent;
    border: none;
    border-radius: 4px;
}
QFrame#gitSectionHead:hover {
    background: rgba(255, 255, 255, 0.03);
}
QLabel#gitSectionTitle {
    color: #8e8e93;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QLabel#gitFileName {
    font-size: 13px;
    font-weight: 500;
    color: #e8e8ed;
}
QLabel#gitFileName[letter="M"] { color: #e2a55d; }
QLabel#gitFileName[letter="U"], QLabel#gitFileName[letter="A"] { color: #73c991; }
QLabel#gitFileName[letter="D"] { color: #f14c4c; }
QLabel#gitFileName[letter="R"], QLabel#gitFileName[letter="C"] { color: #73c991; }
QLabel#gitLetterM { color: #e2a55d; font-weight: 700; font-size: 11px; }
QLabel#gitLetterU, QLabel#gitLetterA, QLabel#gitLetterR, QLabel#gitLetterC {
    color: #73c991;
    font-weight: 700;
    font-size: 11px;
}
QLabel#gitLetterD { color: #f14c4c; font-weight: 700; font-size: 11px; }
QPushButton#gitChevron {
    background: transparent;
    border: none;
    color: #8e8e93;
    padding: 0;
    font-size: 12px;
    font-weight: 700;
}
QPushButton#gitChevron:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #fff;
}
QPushButton#gitRowBtn {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0;
    color: #e8e8ed;
}
QPushButton#gitRowBtn:hover {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(34, 211, 238, 0.28);
}

QFrame#composer {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
}
QFrame#composer:focus-within {
    border: 1px solid rgba(34, 211, 238, 0.45);
}
QPlainTextEdit#composerEdit {
    background: transparent;
    border: none;
    color: #f5f5f7;
    font-size: 13px;
    padding: 4px 4px 4px 2px;
}
QPushButton#composerSend {
    background: rgba(34, 211, 238, 0.22);
    border: 1px solid rgba(34, 211, 238, 0.38);
    border-radius: 8px;
    padding: 0;
}
QPushButton#composerSend:hover { background: rgba(34, 211, 238, 0.36); }
QPushButton#composerStop {
    background: rgba(244, 114, 182, 0.12);
    border: 1px solid rgba(244, 114, 182, 0.28);
    border-radius: 8px;
    padding: 0;
}
QPushButton#composerStop:hover {
    background: rgba(255, 95, 87, 0.28);
    border-color: rgba(255, 95, 87, 0.55);
}
QWidget#terminalPane {
    background: #090b12;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
}
QWidget#terminalHeader {
    background: #0c0e16;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    min-height: 28px;
}
QLabel#terminalCwd {
    color: #8e8e93;
    font-size: 11px;
    padding: 0 4px;
}
QComboBox#terminalCwdPick {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    padding: 1px 8px;
    min-height: 22px;
    max-height: 24px;
    color: #c7c7cc;
    font-size: 11px;
}
QComboBox#terminalCwdPick::drop-down { border: none; width: 16px; }
QWidget#terminalPane QTabWidget::pane {
    border: none;
    border-radius: 0;
    background: #090b12;
    top: 0;
}
QWidget#terminalPane QTabBar::tab {
    background: transparent;
    color: #8e8e93;
    padding: 5px 12px;
    margin: 0 2px 0 0;
    border: none;
    border-radius: 0;
    border-bottom: 1px solid transparent;
}
QWidget#terminalPane QTabBar::tab:selected {
    background: transparent;
    color: #f5f5f7;
    border-bottom: 1px solid #22d3ee;
}
QWidget#terminalPane QTabBar::tab:hover {
    color: #f5f5f7;
}
QPlainTextEdit#terminalView {
    background: #090b12;
    border: none;
    border-radius: 0;
    padding: 6px 12px;
    font-size: 12px;
    color: #e8e8ed;
    font-family: 'Cascadia Mono', Consolas, monospace;
}
"""
