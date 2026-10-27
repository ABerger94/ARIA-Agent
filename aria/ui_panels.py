"""Native settings and live command catalog, built for ARIA's own services."""
from __future__ import annotations

import os


def command_rows():
    # TOOL_TO_TOOLKIT lives in dispatch (it inverts schemas'
    # TOOLKITS there) — importing it from schemas was an
    # ImportError on the laptop (Alek, 2026-10-27) that the
    # stubbed tests masked for months.
    from aria.tools.dispatch import get_registered_tools, TOOL_TO_TOOLKIT
    from aria.tools.schemas import ALL_FUNCTION_DECLARATIONS, COMMAND_GUIDE
    registered = get_registered_tools()
    descriptions = {d['name']: d.get('description', '') for d in ALL_FUNCTION_DECLARATIONS}
    examples = {name: (category, example) for category, name, example in COMMAND_GUIDE}
    return [{'name': name, 'description': descriptions.get(name, 'Tool provided by a connected extension.'),
             'category': examples.get(name, (TOOL_TO_TOOLKIT.get(name, 'Other'), ''))[0],
             'example': examples.get(name, ('', ''))[1],
             'source': 'MCP' if name.startswith('mcp_') else 'ARIA'}
            for name in sorted(registered)]


def create_commands_dialog(parent, choose, rows=None):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                QLineEdit, QComboBox, QScrollArea, QWidget,
                                QFrame, QPushButton)
    rows = command_rows() if rows is None else rows
    dialog = QDialog(parent); dialog.setWindowTitle('ARIA | Commands'); dialog.resize(720, 680)
    layout = QVBoxLayout(dialog); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
    title = QLabel('COMMANDS'); title.setObjectName('title'); layout.addWidget(title)
    count = QLabel(f'{len(rows)} tools available in this session. Click a command to run it.'); count.setWordWrap(True); layout.addWidget(count)
    filters = QHBoxLayout()
    dialog.search = QLineEdit(); dialog.search.setPlaceholderText('Search by command, capability, or example…'); filters.addWidget(dialog.search, 1)
    dialog.category = QComboBox(); dialog.category.addItems(['All categories'] + sorted({r['category'] for r in rows})); filters.addWidget(dialog.category); layout.addLayout(filters)
    area = QScrollArea(); area.setWidgetResizable(True); content = QWidget(); cards = QVBoxLayout(content); cards.setSpacing(12)
    dialog.rows = []
    for row in rows:
        card = QFrame(); card.setObjectName('card'); lay = QVBoxLayout(card); lay.setContentsMargins(16, 14, 16, 14)
        head = QHBoxLayout(); name = QLabel(row['name']); name.setTextFormat(Qt.TextFormat.PlainText); name.setObjectName('title'); head.addWidget(name, 1)
        source = QLabel(row['source'] + ' / ' + row['category']); source.setTextFormat(Qt.TextFormat.PlainText); head.addWidget(source); lay.addLayout(head)
        desc = QLabel(row['description']); desc.setTextFormat(Qt.TextFormat.PlainText); desc.setWordWrap(True); lay.addWidget(desc)
        if row['example']:
            example = QPushButton('Try: ' + row['example']); example.setStyleSheet('text-align:left;'); example.setToolTip('Draft this example in the command bar; nothing runs until you send it.')
            example.clicked.connect(lambda checked=False, text=row['example']: (choose(text), dialog.accept())); lay.addWidget(example)
        cards.addWidget(card)
        dialog.rows.append((card, row['category'], ' '.join(str(v) for v in row.values()).casefold()))
    cards.addStretch(); area.setWidget(content); layout.addWidget(area, 1)
    dialog.empty = QLabel('No commands match this filter.'); dialog.empty.hide(); layout.addWidget(dialog.empty)
    close = QPushButton('Close'); close.clicked.connect(dialog.reject); layout.addWidget(close)
    def apply_filter():
        query = dialog.search.text().strip().casefold(); category = dialog.category.currentText(); visible = 0
        for card, group, haystack in dialog.rows:
            show = query in haystack and (category == 'All categories' or category == group)
            card.setVisible(show); visible += show
        dialog.empty.setVisible(visible == 0)
        count.setText(f'{visible} of {len(rows)} available tools · examples are drafts')
    dialog.search.textChanged.connect(apply_filter); dialog.category.currentTextChanged.connect(apply_filter)
    return dialog


def build_extensions_widget(parent=None):
    """The plugin surface this build actually has: MCP servers
    (aria/mcp.py) over the built-in tool catalog. Read-only —
    management stays with ARIA's mcp_* tools. (Moved here from
    the dashboard overlay at Alek's word, 2026-10-27: Extensions
    belongs in Settings; the header buttons are gone.)"""
    from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
    w = QWidget(parent)
    lay = QVBoxLayout(w)
    try:
        from aria import mcp
        servers = mcp.get_servers()
    except Exception:
        servers = {}
    if servers:
        for name, cfg in sorted(servers.items()):
            kind = cfg.get('transport', 'stdio')
            target = cfg.get('url') or cfg.get('command', '')
            lay.addWidget(QLabel(f'● {name} — {kind} — {target}'))
    else:
        lay.addWidget(QLabel('No MCP servers configured.'))
    try:
        from aria.tools import dispatch as _dispatch
        lay.addWidget(QLabel(
            f'{len(_dispatch.tool_names())} built-in tools registered.'))
    except Exception:
        pass
    hint = QLabel('Manage servers by asking ARIA — '
                  '"connect the filesystem MCP server", '
                  '"list MCP servers", "remove the X server".')
    hint.setWordWrap(True)
    lay.addWidget(hint)
    lay.addStretch()
    return w


def create_settings_dialog(parent, callbacks):
    from zoneinfo import ZoneInfo
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                                QTabWidget, QWidget, QLabel, QLineEdit, QCheckBox,
                                QComboBox, QSlider, QPushButton, QScrollArea)
    from aria import config, speech, hud, approval
    dialog = QDialog(parent); dialog.setWindowTitle('ARIA | Settings'); dialog.resize(740, 660)
    layout = QVBoxLayout(dialog); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(16)
    title = QLabel('SETTINGS'); title.setObjectName('title'); layout.addWidget(title)
    tabs = QTabWidget(); layout.addWidget(tabs, 1)
    def page(name):
        w = QWidget(); form = QFormLayout(w); form.setContentsMargins(18, 22, 18, 22); form.setSpacing(18)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(w); tabs.addTab(scroll, name); return form
    def vpage(name):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(18, 22, 18, 22); lay.setSpacing(14)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(w); tabs.addTab(scroll, name); return lay
    general = page('General')
    dialog.startup = QCheckBox('Read a daily briefing when ARIA starts'); dialog.startup.setChecked(bool(config.get_setting('startup_briefing', True))); general.addRow(dialog.startup)
    dialog.safe = QCheckBox('Safe mode — block shell, GUI control, email sending, and robot motion'); dialog.safe.setChecked(config.is_safe_mode()); general.addRow(dialog.safe)
    if os.environ.get('ARIA_SAFE_MODE', '').lower() in ('1', 'true', 'yes'):
        dialog.safe.setEnabled(False); dialog.safe.setToolTip('Safe mode is forced by the ARIA_SAFE_MODE environment variable.')
    dialog.approval = QComboBox(); dialog.approval.addItems(list(approval.MODES)); dialog.approval.setCurrentText(approval.get_mode()); general.addRow('Action approvals', dialog.approval)
    dialog.accent = QComboBox()
    for label, color in [('Cyan', '#54d9e8'), ('Blue', '#58a6ff'), ('Violet', '#bd93f9'), ('Amber', '#edbc65')]: dialog.accent.addItem(label, color)
    for i in range(dialog.accent.count()):
        if dialog.accent.itemData(i) == config.get_setting('ui_accent', '#54d9e8'): dialog.accent.setCurrentIndex(i)
    general.addRow('Interface accent', dialog.accent)
    # Add desktop icon (Mark-LVI port, Alek 2026-10-27): acts
    # IMMEDIATELY on click, like the Pet page's toggle — not via
    # Apply. The result line lands in the dialog's status label.
    dialog.desktop_icon = QPushButton('Add desktop icon')
    def _add_desktop_icon():
        try:
            from aria import desktop_shortcut as _dsc
            dialog.message.setText(_dsc.create_desktop_shortcut())
        except Exception as exc:
            dialog.message.setText(f'Shortcut failed — {exc}')
    dialog.desktop_icon.clicked.connect(_add_desktop_icon)
    general.addRow(dialog.desktop_icon)
    audio = page('Voice / audio')
    dialog.voice = QCheckBox('Voice and wake-word listening enabled'); dialog.voice.setChecked(bool(speech.VOICE_ENABLED)); audio.addRow(dialog.voice)
    dialog.whisper = QCheckBox('Whisper mode'); dialog.whisper.setChecked(bool(hud.WHISPER_MODE)); audio.addRow(dialog.whisper)
    dialog.volume = QSlider(Qt.Orientation.Horizontal); dialog.volume.setRange(0, 100); dialog.volume.setValue(int(hud.get_hud_volume() * 100)); audio.addRow('System volume', dialog.volume)
    dialog.tts = QComboBox()
    for name in ('en-US-AriaNeural', 'en-US-MichelleNeural', 'en-US-JennyNeural', 'en-US-GuyNeural', 'en-GB-SoniaNeural', 'en-GB-RyanNeural'): dialog.tts.addItem(name)
    dialog.tts.setCurrentText(config.get_setting('tts_voice', 'en-US-AriaNeural')); audio.addRow('Edge voice', dialog.tts)
    test_voice = QPushButton('Test saved voice'); test_voice.clicked.connect(lambda: callbacks.speak('ARIA is connected. Your voice settings are ready.')); audio.addRow(test_voice)
    # The Audio panel's device chooser, embedded (Alek, 2026-10-27:
    # the Providers / Audio / Customize / Pet functions belong in
    # Settings too, not only behind the dashboard's bottom-row
    # buttons). One shared widget builder — no second implementation.
    try:
        from aria.audio_panel import build_audio_widget
        dialog.audio_widget = build_audio_widget(dialog)
        audio.addRow(dialog.audio_widget)
    except Exception:
        dialog.audio_widget = None
    prov_lay = vpage('Providers')
    prov_note = QLabel('Read-only: the live provider chain, each provider’s state and model, and failover stats. Keys live on the Connections tab; chain order lives in config.'); prov_note.setWordWrap(True); prov_lay.addWidget(prov_note)
    try:
        from aria.providers_panel import build_providers_widget
        dialog.providers_widget = build_providers_widget(dialog)
        prov_lay.addWidget(dialog.providers_widget, 1)
    except Exception:
        dialog.providers_widget = None
        prov_lay.addWidget(QLabel('Provider chain unavailable.'))
    ext_lay = vpage('Extensions')
    ext_note = QLabel('What extends ARIA: configured MCP servers over the built-in tool catalog. Read-only here — manage servers by asking ARIA.'); ext_note.setWordWrap(True); ext_lay.addWidget(ext_note)
    try:
        dialog.extensions_widget = build_extensions_widget(dialog)
        ext_lay.addWidget(dialog.extensions_widget, 1)
    except Exception:
        dialog.extensions_widget = None
        ext_lay.addWidget(QLabel('Extensions unavailable.'))
    cust_lay = vpage('Customize')
    cust_note = QLabel('UI accent — one colour re-derives the whole family (hue shift). Picking previews live; Apply settings saves it. The General tab’s preset combo sets the same accent.'); cust_note.setWordWrap(True); cust_lay.addWidget(cust_note)
    dialog._customize_hex = None
    try:
        from aria.hue_wheel import HueWheel
        from aria.customize_panel import PRESETS
        dialog.customize_wheel = HueWheel(str(config.get_setting('ui_accent', '#54d9e8')), dialog)
        cust_lay.addWidget(dialog.customize_wheel, 0, Qt.AlignmentFlag.AlignCenter)
        dialog.customize_hex_label = QLabel(dialog.customize_wheel.color()); dialog.customize_hex_label.setAlignment(Qt.AlignmentFlag.AlignCenter); cust_lay.addWidget(dialog.customize_hex_label)
        def _cust_picked(hex_str):
            dialog._customize_hex = hex_str
            dialog.customize_hex_label.setText(hex_str)
            try:
                parent.apply_accent(hex_str)
            except Exception:
                pass
        dialog.customize_wheel.hue_picked.connect(_cust_picked)
        presets = QHBoxLayout()
        for pname, phex in PRESETS:
            btn = QPushButton(pname)
            btn.clicked.connect(lambda checked=False, h=phex: (dialog.customize_wheel.set_color(h), _cust_picked(h)))
            presets.addWidget(btn)
        cust_lay.addLayout(presets)
    except Exception:
        dialog.customize_wheel = None
        cust_lay.addWidget(QLabel('Customize wheel unavailable — use the accent presets on the General tab.'))
    # Centerpiece (Alek, 2026-10-27: the dashboard's Centerpiece
    # button moved here, under Customize). Applies immediately,
    # like the Pet form combo: persist via ui_prefs, then swap
    # the running dashboard's face through the parent's hook
    # when it offers one.
    cust_cp_note = QLabel('Centerpiece — the face in the middle of the dashboard. Applies immediately.'); cust_cp_note.setWordWrap(True); cust_lay.addWidget(cust_cp_note)
    centerpiece_row = QHBoxLayout(); centerpiece_row.addWidget(QLabel('Centerpiece'))
    dialog.centerpiece = QComboBox(); dialog.centerpiece.addItem('Waveform orb', 'orb'); dialog.centerpiece.addItem('Character', 'holo'); dialog.centerpiece.addItem('Memory map', 'map'); dialog.centerpiece.addItem('Waveform orb 3D', 'orb3d')
    try:
        from aria import ui_prefs as _up_cp
        _cur_cp = _up_cp.get_centerpiece()
    except Exception:
        _cur_cp = 'orb'
    _cp_idx = dialog.centerpiece.findData(_cur_cp)
    dialog.centerpiece.setCurrentIndex(_cp_idx if _cp_idx >= 0 else 0)
    def _centerpiece_changed(idx):
        form = dialog.centerpiece.itemData(idx)
        try:
            from aria import ui_prefs as _up_cp2
            _up_cp2.set_centerpiece(form)
        except Exception:
            pass
        _apply_cp = getattr(parent, 'set_centerpiece', None)
        if callable(_apply_cp):
            try:
                _apply_cp(form)
            except Exception:
                pass
    dialog.centerpiece.currentIndexChanged.connect(_centerpiece_changed)
    centerpiece_row.addWidget(dialog.centerpiece, 1); cust_lay.addLayout(centerpiece_row)
    cust_lay.addStretch()
    pet_lay = vpage('Pet')
    pet_note = QLabel('Desktop pet — the floating mini-ARIA. The same controls as the dashboard’s Pet button and the pet’s right-click menu.'); pet_note.setWordWrap(True); pet_lay.addWidget(pet_note)
    dialog.pet_toggle = QPushButton('Show / hide pet')
    _toggle_pet = getattr(parent, 'toggle_pet', None)
    if callable(_toggle_pet):
        dialog.pet_toggle.clicked.connect(lambda: _toggle_pet())
    else:
        dialog.pet_toggle.setEnabled(False)
    pet_lay.addWidget(dialog.pet_toggle)
    pet_form_row = QHBoxLayout(); pet_form_row.addWidget(QLabel('Pet form'))
    dialog.pet_form = QComboBox(); dialog.pet_form.addItem('Waveform orb', 'orb'); dialog.pet_form.addItem('Character', 'holo')
    try:
        from aria import ui_prefs as _uprefs
        _cur_form = _uprefs.get_centerpiece()
    except Exception:
        _cur_form = 'orb'
    dialog.pet_form.setCurrentIndex(1 if _cur_form == 'holo' else 0)
    def _pet_form_changed(idx):
        form = dialog.pet_form.itemData(idx)
        try:
            from aria import ui_prefs as _up2
            _up2.set_centerpiece(form)
        except Exception:
            pass
        pet = getattr(parent, '_pet', None)
        if pet is not None:
            try:
                pet.set_form(form)
            except Exception:
                pass
    dialog.pet_form.currentIndexChanged.connect(_pet_form_changed)
    pet_form_row.addWidget(dialog.pet_form, 1); pet_lay.addLayout(pet_form_row)
    pet_lay.addStretch()
    brief = page('Daily briefing')
    dialog.name = QLineEdit(str(config.get_setting('briefing_name', 'Alek'))); brief.addRow('Your name', dialog.name)
    dialog.city = QLineEdit(str(config.get_setting('briefing_city', 'Dundalk, Maryland'))); brief.addRow('Local news & weather', dialog.city)
    dialog.zone = QLineEdit(str(config.get_setting('briefing_timezone', 'America/New_York'))); brief.addRow('Time zone', dialog.zone)
    topics = config.get_setting('briefing_world_topics', []); topics = topics if isinstance(topics, list) else []
    dialog.topics = QLineEdit(', '.join(topics)); dialog.topics.setPlaceholderText('Optional: science, technology, world economy'); brief.addRow('World topics', dialog.topics)
    info = QLabel('Today’s schedule, important recent mail, local weather, and dated local/world headlines. Calendar and Gmail connections are configured in Connections.'); info.setWordWrap(True); brief.addRow(info)
    connections = page('Connections')
    connection_fields = {}
    for key, label, secret in [('GMAIL_USER', 'Gmail address', False), ('GMAIL_APP_PASSWORD', 'Gmail app password', True), ('ICAL_URL', 'Calendar iCal URL', True), ('OLLAMA_API_KEY', 'Ollama Cloud key', True), ('GROQ_API_KEY', 'Groq key', True), ('OPENROUTER_API_KEY', 'OpenRouter key', True), ('MISTRAL_API_KEY', 'Mistral key', True), ('GEMINI_API_KEY', 'Gemini key', True)]:
        value, origin = config.key_get(key, '')
        field = QLineEdit(); connection_fields[key] = field
        if secret:
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText('Configured · leave blank to keep' if value and value != 'INSERT' else 'Not connected')
        else:
            field.setText(value if value != 'INSERT' else '')
        if origin.startswith('env var'):
            field.setEnabled(False); field.setPlaceholderText('Managed by environment variable')
        connections.addRow(label, field)
    dialog.connection_fields = connection_fields
    notice = QLabel('Blank secret fields keep existing values. Gmail uses an app password. Calendar requires a private iCal URL. Provider key changes apply after restarting ARIA.'); notice.setWordWrap(True); connections.addRow(notice)
    remote = QPushButton('Open Remote Control'); remote.clicked.connect(parent.open_remote); connections.addRow(remote)
    dialog.message = QLabel(''); dialog.message.setWordWrap(True); layout.addWidget(dialog.message)
    actions = QHBoxLayout(); actions.addStretch()
    cancel = QPushButton('Close'); cancel.clicked.connect(dialog.reject); actions.addWidget(cancel)
    dialog.save = QPushButton('Apply settings'); dialog.save.setObjectName('primary'); actions.addWidget(dialog.save); layout.addLayout(actions)
    def save():
        try:
            ZoneInfo(dialog.zone.text().strip())
            if not dialog.name.text().strip() or not dialog.city.text().strip():
                raise ValueError('Your name and location cannot be empty.')
            updates = {key: field.text().strip() for key, field in connection_fields.items() if field.isEnabled() and field.text().strip()}
            if 'ICAL_URL' in updates and not updates['ICAL_URL'].startswith(('https://', 'http://')):
                raise ValueError('Calendar URL must start with https:// or http://.')
            if updates:
                previous = dict(config._KEYS); config._KEYS.update(updates)
                if not config.save_keys():
                    config._KEYS.clear(); config._KEYS.update(previous)
                    raise OSError('Connection settings could not be saved.')
            values = config.settings_load()
            # The Customize wheel (when touched) wins over the
            # General tab's preset combo — both set the same accent.
            final_accent = getattr(dialog, '_customize_hex', None) or dialog.accent.currentData()
            values.update(startup_briefing=dialog.startup.isChecked(), safe_mode=dialog.safe.isChecked(),
                          voice_enabled=dialog.voice.isChecked(), ui_accent=final_accent,
                          tts_voice=dialog.tts.currentText(), briefing_name=dialog.name.text().strip(),
                          briefing_city=dialog.city.text().strip(), briefing_timezone=dialog.zone.text().strip(),
                          briefing_world_topics=[t.strip() for t in dialog.topics.text().split(',') if t.strip()][:4])
            if not config.settings_save(values): raise OSError('Settings could not be saved.')
            approval.set_mode(dialog.approval.currentText())
            speech.VOICE_ENABLED = dialog.voice.isChecked()
            speech.EDGE_TTS_VOICE = dialog.tts.currentText()
            # Drop the probe-proven session voice so the just-saved
            # choice takes effect on the next utterance, not only
            # after a restart (synthesis reads the configured voice
            # until the next probe proves one).
            try:
                speech._set_session_voice(None)
            except Exception:
                pass
            if hud.WHISPER_MODE != dialog.whisper.isChecked(): callbacks.toggle_whisper()
            hud.set_hud_volume(dialog.volume.value() / 100)
            parent.apply_accent(final_accent)
            # Boot reads the accent from ui_prefs BEFORE settings, so
            # sync it too — otherwise a Settings-chosen accent would
            # silently revert on the next launch.
            try:
                from aria import ui_prefs as _up3
                _up3.set_accent(final_accent)
            except Exception:
                pass
            for key, field in connection_fields.items():
                if key != 'GMAIL_USER': field.clear()
            dialog.message.setText('Settings saved. Provider key changes apply after restarting ARIA.' if any(k.endswith('_API_KEY') for k in updates) else 'Settings saved and applied.')
        except Exception as exc:
            dialog.message.setText(str(exc))
    dialog.save.clicked.connect(save)
    return dialog
