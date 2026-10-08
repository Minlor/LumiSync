"""Connect personal vendor accounts, authorize QR login, and import lights."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QLocale, QSize, Qt, Slot, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QBoxLayout, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from ...accounts.manager import account_manager, make_client
from ...accounts.countries import COUNTRY_CALLING_CODES
from ...accounts.errors import AccountError
from ...accounts.govee import GoveeAccountClient
from ...accounts.privacy import mask_account_label, mask_email
from ...accounts.tuya import TuyaSharingClient
from ...accounts.secrets import vault
from ..controllers.background import start_task
from ..utils.account_country import suggested_account_country
from ..widgets.product_controls import ProductComboBox


PROVIDER_NAMES = {
    "govee_account": "Govee Home", "tuya_account": "Tuya Smart",
    "lsc_account": "LSC Smart Connect", "govee_api": "Govee API",
    "tuya_qr": "Tuya / Smart Life", "tuya_project": "Tuya cloud project",
}
PROVIDER_BRANDS = {
    "govee_account": "govee", "govee_api": "govee", "tuya_account": "tuya",
    "tuya_qr": "tuya", "lsc_account": "lsc",
}
SIGN_IN_METHODS = {
    "govee": (("Email and password", "govee_account"), ("API key", "govee_api")),
    "tuya": (("Email and password", "tuya_account"), ("QR sign-in", "tuya_qr")),
    "lsc": (("Email and password", "lsc_account"),),
}


class AccountFeedback(QLabel):
    """Keep an empty feedback area out of the form's reading order."""

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        super().setText(text)
        self.setVisible(bool(text))

    def clear(self) -> None:
        super().clear()
        self.hide()


class AccountListDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt API
        super().initStyleOption(option, index)
        option.text = ""  # The two-line item widget supplies the visible text.


@dataclass
class SignInFailure:
    message: str
    code: str
    client: object = None
    password: str = field(default="", repr=False)


class AccountsDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Accounts · LumiSync")
        self.setObjectName("AccountsDialog")
        self.setMinimumSize(480, 440)
        self._task = None
        self._busy = False
        self._qr_client = None
        self._qr_created = 0.0
        self._pending_client = None
        self._pending_context = None
        self._pending_password = ""
        self._verification_deadline = 0.0
        self._country_choices = {}
        self._stacked = None
        self._compact = False
        self._account_page = "signin"
        self._suggested_country, self._country_source = suggested_account_country()
        self._verification_timer = QTimer(self)
        self._verification_timer.setSingleShot(True)
        self._verification_timer.timeout.connect(self._expire_verification)
        self._build()
        self._refresh_accounts()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(24, 24, 24, 16)
        outer.setSpacing(18)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        header = QLabel("Accounts")
        header.setProperty("role", "title")
        heading.addWidget(header)
        intro = QLabel("Bring your lights, switches and plugs into LumiSync.")
        intro.setProperty("role", "pageDescription")
        intro.setWordWrap(True)
        heading.addWidget(intro)
        self._intro = intro
        outer.addLayout(heading)
        self.page_switch = QWidget()
        pages = QHBoxLayout(self.page_switch)
        pages.setContentsMargins(0, 0, 0, 0)
        pages.setSpacing(8)
        self.page_buttons = {}
        page_group = QButtonGroup(self)
        page_group.setExclusive(True)
        for key, text in (("signin", "Sign in"), ("connected", "Connected accounts")):
            button = QPushButton(text)
            button.setProperty("role", "accountProvider")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setMinimumHeight(44)
            button.clicked.connect(lambda _checked=False, page=key: self._select_page(page))
            page_group.addButton(button)
            self.page_buttons[key] = button
            pages.addWidget(button, 1)
        self.page_buttons["signin"].setChecked(True)
        self.page_switch.hide()
        outer.addWidget(self.page_switch)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._scroll = scroll
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        self._columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, content)
        self._columns.setContentsMargins(0, 0, 0, 0)
        self._columns.setSpacing(24)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.connected_panel = QWidget()
        self.connected_panel.setMinimumWidth(200)
        self.connected_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        connected = QVBoxLayout(self.connected_panel)
        self._connected_layout = connected
        connected.setContentsMargins(0, 4, 0, 0)
        connected.setSpacing(12)
        self.connected_title = QLabel("Connected accounts")
        self.connected_title.setProperty("role", "sectionTitle")
        connected.addWidget(self.connected_title)
        self.accounts_empty = QLabel("No accounts connected yet. Sign in to import your devices.")
        self.accounts_empty.setProperty("role", "subtle")
        self.accounts_empty.setWordWrap(True)
        connected.addWidget(self.accounts_empty)
        self.accounts_list = QListWidget()
        self.accounts_list.setObjectName("ConnectedAccounts")
        self.accounts_list.setItemDelegate(AccountListDelegate(self.accounts_list))
        self.accounts_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.accounts_list.setAccessibleName("Connected vendor accounts")
        self.accounts_list.currentRowChanged.connect(self._account_selection_changed)
        connected.addWidget(self.accounts_list)
        self.refresh_button = QPushButton("Refresh devices")
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.setMinimumHeight(44)
        self.refresh_button.clicked.connect(self._refresh_selected)
        self._account_actions = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._account_actions.setSpacing(8)
        self._account_actions.addWidget(self.refresh_button)
        self.disconnect_button = QPushButton("Disconnect account")
        self.disconnect_button.setAutoDefault(False)
        self.disconnect_button.setProperty("role", "ghost")
        self.disconnect_button.setMinimumHeight(44)
        self.disconnect_button.clicked.connect(self._disconnect_selected)
        self._account_actions.addWidget(self.disconnect_button)
        connected.addLayout(self._account_actions)
        account_note = QLabel("Refresh an account to pick up new devices and their available controls.")
        account_note.setWordWrap(True)
        account_note.setProperty("role", "subtle")
        self._account_note = account_note
        connected.addWidget(account_note)
        connected.addStretch(1)
        self._columns.addWidget(self.connected_panel, 2, Qt.AlignmentFlag.AlignTop)

        self.signin_panel = QFrame()
        self.signin_panel.setObjectName("AccountSignInPanel")
        self.signin_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        root = QVBoxLayout(self.signin_panel)
        self._signin_layout = root
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(12)
        self.signin_title = QLabel("Sign in to Govee Home")
        self.signin_title.setProperty("role", "inspectorTitle")
        self.signin_title.setWordWrap(True)
        root.addWidget(self.signin_title)
        provider_row = QHBoxLayout()
        provider_row.setSpacing(8)
        self.provider_buttons = {}
        self._provider_group = QButtonGroup(self)
        self._provider_group.setExclusive(True)
        for brand, label in (("govee", "Govee"), ("tuya", "Tuya"), ("lsc", "LSC")):
            button = QPushButton(label)
            button.setProperty("role", "accountProvider")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setMinimumHeight(44)
            button.setAccessibleName("Use " + label + " account")
            button.clicked.connect(lambda _checked=False, choice=brand: self._select_brand(choice))
            self.provider_buttons[brand] = button
            self._provider_group.addButton(button)
            provider_row.addWidget(button, 1)
        root.addLayout(provider_row)
        # One provider value owns the existing authentication state machine.
        # The visible brand and method controls select that value together.
        self.provider = ProductComboBox(self.signin_panel)
        for provider, label in PROVIDER_NAMES.items():
            if provider != "tuya_project":
                self.provider.addItem(label, provider)
        self.provider.hide()
        self.method_row = QWidget()
        method_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self.method_row)
        self._method_layout = method_layout
        method_layout.setContentsMargins(0, 0, 0, 0)
        method_layout.setSpacing(6)
        method_label = QLabel("Sign-in method")
        method_label.setProperty("role", "fieldLabel")
        self.method = ProductComboBox()
        self.method.setMinimumHeight(44)
        self.method.setAccessibleName("Sign-in method")
        method_label.setBuddy(self.method)
        self._method_label = method_label
        self.method.currentIndexChanged.connect(self._method_changed)
        method_layout.addWidget(method_label)
        method_layout.addWidget(self.method)
        root.addWidget(self.method_row)
        self.help = QLabel()
        self.help.setProperty("role", "subtle")
        self.help.setWordWrap(True)
        root.addWidget(self.help)

        form = QVBoxLayout()
        self._form_layout = form
        form.setSpacing(12)
        self.country = ProductComboBox()
        self.country.setMinimumHeight(44)
        self.country.setAccessibleName("Account country")
        self.country.setSizeAdjustPolicy(self.country.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.country.setMinimumContentsLength(16)
        self.country.addItem("Select your account country", "")
        countries = [(QLocale.territoryToString(QLocale.codeToTerritory(iso)), iso, code)
                     for iso, code in COUNTRY_CALLING_CODES]
        for name, iso, code in sorted(countries, key=lambda item: (item[0].casefold(), item[2])):
            self.country.addItem(f"{name} (+{code})", code)
            self.country.setItemData(self.country.count() - 1, iso, Qt.ItemDataRole.UserRole + 1)
        selected_country = self.country.findData(self._suggested_country, Qt.ItemDataRole.UserRole + 1)
        self.country.setCurrentIndex(max(0, selected_country))
        self.country_row = QWidget()
        country_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self.country_row)
        self._country_layout = country_layout
        country_layout.setContentsMargins(0, 0, 0, 0)
        country_layout.setSpacing(6)
        self.country_label = QLabel("Account country")
        self.country_label.setProperty("role", "fieldLabel")
        self.country_label.setBuddy(self.country)
        self.country.setToolTip("Choose the same country as in your Tuya or LSC app. Account routing is automatic.")
        country_layout.addWidget(self.country_label)
        country_layout.addWidget(self.country)
        self.country_hint = QLabel()
        self.country_hint.setProperty("role", "subtle")
        self.country_hint.setWordWrap(True)
        country_layout.addWidget(self.country_hint)
        self.entries = {}
        self.labels = {}
        self.field_rows = {}
        self._field_layouts = {}
        for key, title, secret in (
            ("email", "Email", False), ("password", "Password", True),
            ("mfa_code", "Email verification code", False),
            ("api_key", "Govee API key", True), ("user_code", "App account user code", False),
        ):
            if key == "password":
                form.addWidget(self.country_row)
            field = QWidget()
            field_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, field)
            self._field_layouts[key] = field_layout
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(6)
            entry = QLineEdit()
            entry.setMinimumHeight(44)
            entry.setAccessibleName(title)
            if secret:
                entry.setEchoMode(QLineEdit.EchoMode.Password)
            label = QLabel(title)
            label.setProperty("role", "fieldLabel")
            label.setBuddy(entry)
            self.entries[key] = entry
            self.labels[key] = label
            self.field_rows[key] = field
            field_layout.addWidget(label)
            if key == "password":
                password_row = QHBoxLayout()
                password_row.setSpacing(8)
                password_row.addWidget(entry, 1)
                self.show_password = QToolButton()
                self.show_password.setObjectName("ShowPassword")
                self.show_password.setText("Show")
                self.show_password.setCheckable(True)
                self.show_password.setMinimumSize(56, 44)
                self.show_password.setAccessibleName("Show password")
                self.show_password.toggled.connect(self._toggle_password)
                password_row.addWidget(self.show_password)
                field_layout.addLayout(password_row)
            else:
                field_layout.addWidget(entry)
            entry.returnPressed.connect(lambda: self.connect_button.click())
            form.addWidget(field)
        self.entries["email"].setPlaceholderText("Your account email")
        self.entries["password"].setPlaceholderText("Your account password")
        self.entries["mfa_code"].setPlaceholderText("Code from your latest verification email")
        self.entries["mfa_code"].setMaxLength(12)
        self.entries["mfa_code"].setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.entries["user_code"].setPlaceholderText("User code from your phone app")
        root.addLayout(form)
        self.verification_help = QLabel()
        self.verification_help.setProperty("role", "subtle")
        self.verification_help.setWordWrap(True)
        self.verification_help.hide()
        root.addWidget(self.verification_help)
        self.resend_button = QPushButton("Send a new code")
        self.resend_button.setProperty("role", "ghost")
        self.resend_button.setAutoDefault(False)
        self.resend_button.setMinimumHeight(44)
        self.resend_button.clicked.connect(self._resend_code)
        self.restart_button = QPushButton("Back to sign-in")
        self.restart_button.setProperty("role", "ghost")
        self.restart_button.setAutoDefault(False)
        self.restart_button.setMinimumHeight(44)
        self.restart_button.clicked.connect(self._restart_login)
        self.verification_actions = QWidget()
        verification_actions = QHBoxLayout(self.verification_actions)
        verification_actions.setContentsMargins(0, 0, 0, 0)
        verification_actions.setSpacing(8)
        verification_actions.addWidget(self.resend_button, 1)
        verification_actions.addWidget(self.restart_button, 1)
        root.addWidget(self.verification_actions)
        self.connection_options = QPushButton("App setup options")
        self.connection_options.setObjectName("AccountOptionsToggle")
        self.connection_options.setAutoDefault(False)
        self.connection_options.setMinimumHeight(44)
        self.connection_options.setCheckable(True)
        self.connection_options.setAccessibleName("Show app configuration options")
        self.options_panel = QWidget()
        options_layout = QFormLayout(self.options_panel)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.app_package = QLineEdit()
        self.app_package.setMinimumWidth(80)
        self.app_package.setPlaceholderText("Find automatically in Downloads")
        self.app_package.setAccessibleName("Vendor app package path")
        self.package_row = QWidget()
        package_layout = QHBoxLayout(self.package_row)
        package_layout.setContentsMargins(0, 0, 0, 0)
        package_layout.addWidget(self.app_package, 1)
        self.browse_package = QPushButton("Browse…")
        self.browse_package.setAutoDefault(False)
        self.browse_package.clicked.connect(self._choose_package)
        package_layout.addWidget(self.browse_package)
        self.package_label = QLabel("App configuration file")
        options_layout.addRow(self.package_label, self.package_row)
        options_help = QLabel("LumiSync finds the matching app file in Downloads or uses its saved configuration. Select an APK, APKM or XAPK only if automatic setup fails.")
        options_help.setWordWrap(True)
        options_layout.addRow(options_help)
        self.connection_options.toggled.connect(self.options_panel.setVisible)
        self.remember = QCheckBox("Remember this account")
        self.remember.setMinimumHeight(44)
        self.remember.setToolTip("Save the connection in your system credential store. Your password is not saved.")
        self.remember.setChecked(True)
        self.preferences_row = QWidget()
        preferences = QHBoxLayout(self.preferences_row)
        preferences.setContentsMargins(0, 0, 0, 0)
        preferences.setSpacing(12)
        preferences.addWidget(self.remember, 1)
        preferences.addWidget(self.connection_options)
        root.addWidget(self.preferences_row)
        root.addWidget(self.options_panel)
        self.connect_button = QPushButton("Sign in")
        self.connect_button.setObjectName("Primary")
        self.connect_button.setMinimumHeight(46)
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self._connect)
        self.signin_actions = QWidget()
        action_layout = QVBoxLayout(self.signin_actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addWidget(self.connect_button)
        self.progress = QProgressBar()
        self.progress.setObjectName("AccountProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setAccessibleName("Account request in progress")
        self.progress.hide()
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setAccessibleName("Tuya sign-in QR code")
        self.qr_label.hide()
        root.addWidget(self.qr_label)
        self.qr_confirm = QPushButton("I've approved the code. Finish sign-in")
        self.qr_confirm.setAutoDefault(False)
        self.qr_confirm.setMinimumHeight(44)
        self.qr_confirm.clicked.connect(self._finish_qr)
        self.qr_confirm.hide()
        action_layout.addWidget(self.qr_confirm)
        root.addWidget(self.signin_actions)
        self.status = AccountFeedback()
        self.status.setObjectName("AccountFeedback")
        self.status.setAccessibleName("Account connection feedback")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        self._columns.addWidget(self.signin_panel, 5, Qt.AlignmentFlag.AlignTop)
        self._action_footer = QWidget()
        self._action_footer_layout = QHBoxLayout(self._action_footer)
        self._action_footer_layout.setContentsMargins(0, 0, 0, 0)
        self._action_footer_layout.setSpacing(10)
        self._action_footer.hide()
        outer.addWidget(self._action_footer)
        outer.addWidget(self.progress)
        outer.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._footer_buttons = buttons
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.entries["email"].textEdited.connect(self._clear_pending_login)
        self.country.currentIndexChanged.connect(self._country_changed)
        self.app_package.textEdited.connect(self._clear_pending_login)
        self._provider_changed()
        available = self.screen().availableGeometry()
        self.resize(min(860, max(480, available.width() - 48)),
                    min(800, max(440, available.height() - 72)))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "_columns"):
            self._update_responsive_layout()

    def _select_page(self, page: str) -> None:
        self._account_page = page
        self.page_buttons[page].setChecked(True)
        self._update_responsive_layout()
        self._scroll.verticalScrollBar().setValue(0)

    def _update_responsive_layout(self) -> None:
        stacked = self.width() < 760
        comfortable_height = 840 if self.provider.currentData() == "tuya_account" else 760
        compact = stacked or self.height() < comfortable_height
        self._compact = compact
        self._stacked = stacked
        self.page_switch.setVisible(stacked)
        self._columns.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self.connected_panel.setMaximumWidth(16777215 if stacked else 270)
        self.connected_panel.setVisible(not stacked or self._account_page == "connected")
        self.signin_panel.setVisible(not stacked or self._account_page == "signin")
        self._outer_layout.setContentsMargins(16 if compact else 24, 16 if compact else 24,
                                             16 if compact else 24, 16)
        self._outer_layout.setSpacing(10 if compact else 18)
        self._signin_layout.setContentsMargins(16 if compact else 24, 14 if compact else 22,
                                               16 if compact else 24, 16 if compact else 24)
        self._signin_layout.setSpacing(8 if compact else 12)
        self._form_layout.setSpacing(8 if compact else 12)
        self._connected_layout.setSpacing(8 if compact else 12)
        self._account_note.setVisible(not compact)
        self._account_actions.setDirection(QBoxLayout.Direction.LeftToRight if stacked else QBoxLayout.Direction.TopToBottom)
        account_rows = self.accounts_list.count() * 78 + 8
        available_rows = max(78, self._scroll.viewport().height() - (94 if stacked else 150))
        self.accounts_list.setFixedHeight(min(270, account_rows, available_rows))
        direction = QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom
        for label, layout in [(self._method_label, self._method_layout),
                              (self.country_label, self._country_layout),
                              *[(self.labels[key], layout) for key, layout in self._field_layouts.items()]]:
            label.setWordWrap(True)
            if compact:
                label.setFixedWidth(104)
            else:
                label.setMaximumWidth(16777215)
                label.setMinimumWidth(0)
            layout.setDirection(direction)
        self.country_hint.setVisible(not compact)
        self.country.setToolTip(self.country_hint.text() + " Choose the same country as in your phone app. Routing is automatic.")
        personal = self.provider.currentData() in ("govee_account", "tuya_account", "lsc_account")
        verification = self._pending_client is not None
        self.signin_title.setVisible(not compact or not personal or verification)
        self.help.setVisible(not compact or not personal or verification)
        self._intro.setVisible(self.height() >= 680)
        if compact:
            if self.signin_actions.parentWidget() is not self._action_footer:
                self._signin_layout.removeWidget(self.signin_actions)
                self._action_footer_layout.addWidget(self.signin_actions, 1)
                self._outer_layout.removeWidget(self._footer_buttons)
                self._action_footer_layout.addWidget(self._footer_buttons, 0, Qt.AlignmentFlag.AlignBottom)
                self._outer_layout.removeWidget(self._action_footer)
                self._outer_layout.addWidget(self._action_footer)
        else:
            if self.signin_actions.parentWidget() is self._action_footer:
                self._action_footer_layout.removeWidget(self.signin_actions)
                self._signin_layout.addWidget(self.signin_actions)
                self._action_footer_layout.removeWidget(self._footer_buttons)
                self._outer_layout.addWidget(self._footer_buttons)
        self._action_footer.setVisible(compact)
        self.signin_actions.setVisible(not stacked or self._account_page == "signin")
        self._set_tab_order(stacked)

    def _set_tab_order(self, stacked: bool) -> None:
        account_controls = [self.accounts_list, self.refresh_button, self.disconnect_button]
        sign_in_controls = [*self.provider_buttons.values(), self.method, self.entries["email"], self.country,
                            self.entries["password"], self.show_password, self.entries["mfa_code"],
                            self.entries["api_key"], self.entries["user_code"], self.resend_button, self.restart_button,
                            self.remember, self.connection_options, self.app_package, self.browse_package,
                            self.connect_button, self.qr_confirm]
        ordered = ([*self.page_buttons.values()] if stacked else []) + (
            sign_in_controls + account_controls if stacked else account_controls + sign_in_controls) + [self.close_button]
        for first, second in zip(ordered, ordered[1:]):
            QWidget.setTabOrder(first, second)

    def _select_brand(self, brand: str) -> None:
        self.provider.setCurrentIndex(self.provider.findData(SIGN_IN_METHODS[brand][0][1]))

    def _method_changed(self, *_args) -> None:
        provider = self.method.currentData()
        if provider:
            self.provider.setCurrentIndex(self.provider.findData(provider))

    def _country_changed(self, *_args) -> None:
        provider = self.provider.currentData()
        if provider in ("tuya_account", "lsc_account"):
            self._country_choices[provider] = self.country.currentData(Qt.ItemDataRole.UserRole + 1) or ""
        self.country_hint.setText("Use the country registered in your phone app.")
        self._clear_pending_login()

    def _restore_country(self, provider: str) -> None:
        if provider in self._country_choices:
            country = self._country_choices[provider]
            note = "Use the country registered in your phone app."
        else:
            previous = next((account.get("country_iso") for account in reversed(self.controller.get_accounts())
                             if account.get("provider") == provider and account.get("country_iso")), None)
            country = previous or self._suggested_country
            note = "From your last successful sign-in." if previous else (
                f"Suggested from {self._country_source}. Check it matches your account."
                if country else "Choose the country registered in your phone app.")
        index = self.country.findData(country, Qt.ItemDataRole.UserRole + 1) if country else 0
        if index < 0:
            index = self.country.findData(self._suggested_country, Qt.ItemDataRole.UserRole + 1)
            note = "Choose the country registered in your phone app."
        blocked = self.country.blockSignals(True)
        self.country.setCurrentIndex(max(0, index))
        self.country.blockSignals(blocked)
        self.country_hint.setText(note)

    def _toggle_password(self, visible: bool) -> None:
        self.entries["password"].setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_password.setText("Hide" if visible else "Show")
        self.show_password.setAccessibleName("Hide password" if visible else "Show password")

    def _restart_login(self) -> None:
        self._clear_pending_login()
        self.entries["password"].clear()
        self.status.clear()
        self.entries["password"].setFocus()

    def _set_field_visible(self, key: str, visible: bool) -> None:
        self.field_rows[key].setVisible(visible)
        self.entries[key].setVisible(visible)
        self.labels[key].setVisible(visible)

    def _show_status(self, message: str, state: str = "info") -> None:
        self.status.setProperty("feedbackState", state)
        self.status.setText(message)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _account_selection_changed(self, *_args) -> None:
        enabled = self._selected_account() is not None and not self._busy
        self.refresh_button.setEnabled(enabled)
        self.disconnect_button.setEnabled(enabled)

    def _clear_pending_login(self, *_args) -> None:
        if self._pending_client is not None:
            self._pending_client.close()
            self._pending_client = None
        self._pending_context = None
        self._pending_password = ""
        self._verification_deadline = 0.0
        self._verification_timer.stop()
        self.entries["mfa_code"].clear()
        self._set_field_visible("mfa_code", False)
        self.verification_help.hide()
        self.resend_button.hide()
        self.restart_button.hide()
        self.verification_actions.hide()
        personal = self.provider.currentData() in ("govee_account", "tuya_account", "lsc_account")
        self._set_field_visible("password", personal)
        self._set_field_visible("email", personal)
        self.preferences_row.show()
        mobile = self.provider.currentData() in ("tuya_account", "lsc_account")
        self.country_row.setVisible(mobile)
        self.options_panel.setVisible(mobile and self.connection_options.isChecked())
        self.method_row.setVisible(self.method.count() > 1)
        if hasattr(self, "_provider_help_text"):
            self.help.setText(self._provider_help_text)
        self.show_password.setChecked(False)
        provider = self.provider.currentData()
        self.signin_title.setText("Sign in to " + PROVIDER_NAMES[provider])
        self.connect_button.setText("Connect API key" if provider == "govee_api" else "Get sign-in code" if provider == "tuya_qr" else "Sign in")
        self._update_responsive_layout()

    def _expire_verification(self) -> None:
        if self._busy:
            # An in-flight verification request owns the session until it
            # returns. Do not close its HTTP session from the GUI thread.
            self._verification_timer.start(1000)
            return
        self._clear_pending_login()
        self._show_status("This sign-in attempt has expired. Enter your password to start again.", "error")

    def _provider_changed(self) -> None:
        self._clear_pending_login()
        if self._qr_client is not None:
            self._qr_client.close()
            self._qr_client = None
        provider = self.provider.currentData()
        brand_key = PROVIDER_BRANDS[provider]
        self.provider_buttons[brand_key].setChecked(True)
        blocked = self.method.blockSignals(True)
        self.method.clear()
        for label, value in SIGN_IN_METHODS[brand_key]:
            self.method.addItem(label, value)
        self.method.setCurrentIndex(self.method.findData(provider))
        self.method.blockSignals(blocked)
        self.method_row.setVisible(self.method.count() > 1)
        mobile = provider in ("tuya_account", "lsc_account")
        if mobile:
            self._restore_country(provider)
        visible = {"govee_account": ("email", "password"), "govee_api": ("api_key",),
                   "tuya_account": ("email", "password"),
                   "lsc_account": ("email", "password"),
                   "tuya_qr": ("user_code",)}[provider]
        for key in self.entries:
            self._set_field_visible(key, key in visible)
        brand = {"tuya_account": "Tuya Smart", "lsc_account": "LSC Smart Connect"}.get(provider, "Govee")
        for key in ("email", "password"):
            title = brand + " " + key
            self.labels[key].setText(key.capitalize())
            self.entries[key].setAccessibleName(title)
        self.entries["password"].clear()
        self.entries["mfa_code"].clear()
        self.app_package.clear()
        self.country.setVisible(mobile)
        self.country_label.setVisible(mobile)
        self.country_row.setVisible(mobile)
        self.connection_options.setChecked(False)
        self.connection_options.setVisible(mobile)
        self.options_panel.hide()
        self.help.setText({
            "govee_account": "Use the email and password you use in Govee Home. Your devices appear here after sign-in.",
            "govee_api": "Find your API key in Govee Home: Settings, Apply for API Key. This connects supported Wi-Fi devices.",
            "tuya_account": "Use your Tuya Smart email and password. Choose the same country as in the phone app.",
            "lsc_account": "Use your LSC Smart Connect email and password. Choose the same country as in the phone app.",
            "tuya_qr": "In Tuya Smart or Smart Life, open Me, Settings, Account and Security to copy your user code. The sharing approval screen names Home Assistant.",
        }[provider])
        self._provider_help_text = self.help.text()
        self.qr_label.clear()
        self.qr_label.hide()
        self.qr_confirm.hide()
        self.status.clear()
        self._update_responsive_layout()

    def _choose_package(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select the matching vendor app package", "", "Android app packages (*.apk *.apkm *.xapk)")
        if path:
            self.app_package.setText(path)

    def _refresh_accounts(self, select_id=None) -> None:
        selected = self._selected_account()
        selected_id = select_id or (selected.get("id") if selected else None)
        self.accounts_list.clear()
        for metadata in self.controller.get_accounts():
            title = mask_account_label(metadata.get("label") or metadata["provider"])
            if not metadata.get("remember", True):
                title += " · this session"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, metadata)
            item.setSizeHint(QSize(180, 74))
            item.setToolTip(title)
            self.accounts_list.addItem(item)
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout = QVBoxLayout(row)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(4)
            name = QLabel(PROVIDER_NAMES.get(metadata["provider"], "Connected account"))
            name.setProperty("role", "strong")
            name.setTextFormat(Qt.TextFormat.PlainText)
            detail = QLabel(title.split(" · ", 1)[-1])
            detail.setProperty("role", "subtle")
            detail.setTextFormat(Qt.TextFormat.PlainText)
            detail.setWordWrap(True)
            layout.addWidget(name)
            layout.addWidget(detail)
            self.accounts_list.setItemWidget(item, row)
            if selected_id and metadata.get("id") == selected_id:
                self.accounts_list.setCurrentItem(item)
        has_accounts = self.accounts_list.count() > 0
        self.accounts_list.setFixedHeight(min(270, self.accounts_list.count() * 78 + 8))
        if has_accounts and self.accounts_list.currentRow() < 0:
            self.accounts_list.setCurrentRow(0)
        self.accounts_empty.setVisible(not has_accounts)
        self.page_buttons["connected"].setText(f"Connected accounts ({self.accounts_list.count()})")
        for widget in (self.accounts_list, self.refresh_button, self.disconnect_button):
            widget.setVisible(has_accounts)
        self._account_selection_changed()
        self._update_responsive_layout()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        for widget in (self.connect_button, self.refresh_button, self.disconnect_button, self.provider, self.qr_confirm,
                       self.country, self.connection_options, self.resend_button,
                       self.restart_button,
                       self.app_package, self.browse_package, self.remember, self.accounts_list,
                       self.method, self.show_password, *self.provider_buttons.values(), *self.entries.values()):
            widget.setEnabled(not busy)
        self._account_selection_changed()

    def _run(self, action, completed) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._callback = completed
        self._task = start_task(action, self._completed)

    @Slot(object, object)
    def _completed(self, value, error) -> None:
        self._set_busy(False)
        callback, self._callback = self._callback, None
        self._task = None
        if self.controller._closing:
            if isinstance(value, tuple) and hasattr(value[0], "close"):
                value[0].close()
            elif isinstance(value, SignInFailure) and value.client is not None:
                value.client.close()
            self._clear_pending_login()
            return
        if error:
            self._show_status(error, "error")
            return
        try:
            callback(value)
        except Exception as exc:
            from ...accounts.errors import AccountError
            self._show_status(str(exc) if isinstance(exc, AccountError) else "Could not save the connected account. Check credential storage and try again.", "error")

    def _connect(self, resend: bool = False) -> None:
        if self._busy:
            return
        provider = self.provider.currentData()
        mobile = provider in ("tuya_account", "lsc_account")
        required = {"govee_account": ("email", "password"), "govee_api": ("api_key",),
                    "tuya_account": ("email", "password"),
                    "lsc_account": ("email", "password"),
                    "tuya_qr": ("user_code",)}[provider]
        credentials = {key: (entry.text() if key == "password" else entry.text().strip()) for key, entry in self.entries.items()}
        credentials["country_code"] = self.country.currentData() if mobile else ""
        credentials["app_package"] = self.app_package.text().strip() if mobile else ""
        context = (provider, credentials["email"], credentials["country_code"], credentials["app_package"])
        if self._pending_client is not None and self._verification_deadline <= time.monotonic():
            self._expire_verification()
            return
        pending_client = self._pending_client if self._pending_context == context else None
        deadline = self._verification_deadline
        if pending_client is not None:
            credentials["password"] = self._pending_password
            if not credentials["mfa_code"] and not resend:
                self._show_status("Enter the verification code from your email to continue.", "error")
                self.entries["mfa_code"].setFocus()
                return
        else:
            self._clear_pending_login()
            credentials["mfa_code"] = ""
        if any(not credentials[key] for key in required) or (mobile and not credentials["country_code"]):
            missing = next((key for key in required if not credentials[key]), None)
            title = self.labels[missing].text() if missing else "Account country"
            self._show_status(f"Enter your {title.lower()} to continue." if missing else "Choose your account country to continue.", "error")
            (self.entries[missing] if missing else self.country).setFocus()
            return
        if mobile:
            credentials["region"] = "auto"
        self._remember_pending = self.remember.isChecked()
        self._provider_pending = provider
        self._country_pending = self.country.currentData(Qt.ItemDataRole.UserRole + 1) if mobile else ""
        remember = self._remember_pending
        self._pending_client = None
        self._pending_password = ""
        self._verification_timer.stop()
        self._context_pending = context
        self._deadline_pending = deadline or (time.monotonic() + 15 * 60)
        self._show_status("Connecting to your account…")
        if provider == "tuya_qr":
            if self._qr_client is not None:
                self._qr_client.close()
                self._qr_client = None
            def request_qr():
                client = TuyaSharingClient()
                try:
                    return client, client.request_qr(credentials["user_code"])
                except Exception:
                    client.close()
                    raise
            self._run(request_qr, self._show_qr)
            return
        self.entries["password"].clear()
        self.show_password.setChecked(False)
        self.entries["mfa_code"].clear()
        def connect():
            if pending_client is not None:
                client = pending_client
            elif provider == "govee_account":
                client = GoveeAccountClient()
            elif mobile:
                client = make_client(provider, {})
            else:
                client = make_client(provider, {key: credentials[key] for key in required})
            try:
                if provider == "govee_account":
                    client.login(credentials["email"], credentials["password"], mfa_code=credentials["mfa_code"])
                elif mobile:
                    if pending_client is None:
                        client.load_profile(credentials["app_package"])
                    client.login(credentials["email"], credentials["password"], credentials["country_code"],
                                 mfa_code=credentials["mfa_code"], region=credentials["region"])
                    vault.put("vendor-app/" + provider.removesuffix("_account"), client.credentials["profile"], remember=remember)
                return client, client.list_devices()
            except AccountError as exc:
                if exc.code in ("verification_required", "verification"):
                    return SignInFailure(str(exc), exc.code, client, credentials["password"])
                client.close()
                return SignInFailure(str(exc), exc.code)
            except Exception:
                client.close()
                raise
        self._run(connect, self._save_connection)

    def _resend_code(self) -> None:
        if self._busy or self._pending_client is None:
            return
        if self.entries["email"].text().strip() != self._pending_context[1]:
            self._clear_pending_login()
            return
        client, email = self._pending_client, self._pending_context[1]
        self.entries["mfa_code"].clear()
        if self.provider.currentData() in ("tuya_account", "lsc_account"):
            self._connect(resend=True)
            return
        self._run(lambda: client.request_verification(email),
                  lambda _: self._show_status("A new Govee code was sent. Enter it here to finish signing in."))

    def _show_qr(self, value) -> None:
        import qrcode
        self._qr_client, data = value
        self._qr_created = time.monotonic()
        image = qrcode.make(data)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(stream.getvalue(), "PNG")
        self.qr_label.setPixmap(pixmap.scaled(230, 230, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
        self.qr_label.show()
        self.qr_confirm.show()
        self._show_status("Scan and approve this code in Tuya Smart or Smart Life, then finish sign-in.")
        QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self.qr_label))

    def _finish_qr(self) -> None:
        if self._qr_client is None:
            return
        if time.monotonic() - self._qr_created > 180:
            self._show_status("The QR code has expired. Request a new code.", "error")
            self.qr_confirm.hide()
            self.qr_label.clear()
            self.qr_label.hide()
            self._qr_client.close()
            self._qr_client = None
            return
        client = self._qr_client
        def finish():
            if not client.finish_qr():
                return None
            return client, client.list_devices()
        self._run(finish, self._qr_finished)

    def _qr_finished(self, value) -> None:
        if value is None:
            self._show_status("Authorization is still pending. Approve the code in the phone app, then try again.")
            return
        self._save_connection(value)
        self.qr_label.clear()
        self.qr_label.hide()
        self.qr_confirm.hide()
        self._qr_client = None

    def _save_connection(self, value) -> None:
        if isinstance(value, SignInFailure):
            self._show_status(value.message, "info" if value.client is not None else "error")
            self._pending_client = value.client
            self._pending_context = self._context_pending if value.client is not None else None
            if value.client is not None:
                self._pending_password = value.password
                self._verification_deadline = self._deadline_pending
                remaining = self._verification_deadline - time.monotonic()
                if remaining <= 0:
                    self._expire_verification()
                    return
                self._verification_timer.start(int(remaining * 1000))
                self._set_field_visible("password", False)
                self._set_field_visible("email", False)
                self._set_field_visible("mfa_code", True)
                self.country_row.hide()
                self.method_row.hide()
                self.preferences_row.hide()
                self.options_panel.hide()
                self.signin_title.setText("Verify your email")
                self.help.setText("Confirm your account with the code from your email to finish connecting your devices.")
                self.verification_help.setText("Enter the latest code sent to " + mask_email(self._pending_context[1]) + ".")
                self.verification_help.show()
                self.resend_button.show()
                self.restart_button.show()
                self.verification_actions.show()
                self.entries["mfa_code"].setFocus()
                self.connect_button.setText("Verify and connect")
                self._update_responsive_layout()
                QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self.entries["mfa_code"]))
            else:
                self._clear_pending_login()
            if value.code == "app_profile":
                self.connection_options.setChecked(True)
            return
        client, raw_devices = value
        try:
            metadata = account_manager.register(self._provider_pending, client, remember=self._remember_pending)
        except Exception:
            client.close()
            raise
        metadata["label"] = PROVIDER_NAMES[self.provider.currentData()]
        if self._provider_pending in ("tuya_account", "lsc_account") and self._country_pending:
            metadata["country_iso"] = self._country_pending
        if client.credentials.get("email"):
            metadata["label"] += " · " + mask_email(client.credentials["email"])
        try:
            descriptors = account_manager.descriptors(metadata, raw_devices)
            self.controller.import_account_devices(metadata, descriptors)
        except Exception:
            account_manager.disconnect(metadata["id"])
            raise
        self._refresh_accounts(metadata["id"])
        self._clear_pending_login()
        self.entries["email"].clear()
        count = len(descriptors)
        self._show_status(f"Account connected. {count} supported device{'s' if count != 1 else ''} imported. Open Devices to control them.", "success")
        for key in ("api_key",):
            self.entries[key].clear()

    def _selected_account(self):
        item = self.accounts_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _refresh_selected(self) -> None:
        metadata = self._selected_account()
        if not metadata:
            self._show_status("Choose a connected account first.")
            return
        def refresh():
            client = account_manager.client_for({"account_id": metadata["id"]})
            return account_manager.descriptors(metadata, client.list_devices())
        self._show_status("Refreshing devices from " + PROVIDER_NAMES.get(metadata["provider"], "your account") + "…")
        self._run(refresh, lambda descriptors: self._devices_refreshed(metadata, descriptors))

    def _devices_refreshed(self, metadata, descriptors) -> None:
        self.controller.import_account_devices(metadata, descriptors)
        self._show_status(f"Refreshed {len(descriptors)} account device(s).", "success")

    def _disconnect_selected(self) -> None:
        metadata = self._selected_account()
        if not metadata:
            self._show_status("Choose a connected account first.")
            return
        self._show_status("Disconnecting account…")
        self._run(lambda: account_manager.disconnect(metadata["id"]), lambda _: self._account_disconnected(metadata))

    def _account_disconnected(self, metadata) -> None:
        self.controller.remove_account(metadata["id"])
        self._refresh_accounts()
        self._show_status("Account disconnected. Saved local connections can still be used.", "success")

    def reject(self) -> None:
        if self._busy:
            self._show_status("The current connection request is finishing. Close this dialog when it completes.")
            return
        if self._qr_client is not None:
            self._qr_client.close()
            self._qr_client = None
        self._clear_pending_login()
        super().reject()
