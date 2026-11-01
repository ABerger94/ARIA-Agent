"""Bundled, offline help. Reading and copying examples never dispatches a tool."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape, unescape
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSizePolicy, QSplitter, QTextBrowser,
    QVBoxLayout, QWidget,
)


@dataclass(frozen=True)
class GuideTopic:
    key: str
    title: str
    body: str
    examples: tuple[tuple[str, str], ...] = ()

    def search_text(self):
        text = ' '.join((self.title, self.body, *(label + ' ' + prompt for label, prompt in self.examples)))
        return unescape(re.sub(r'<[^>]+>', ' ', text)).casefold()


TOPICS = (
    GuideTopic('start', 'Start here', """
        <p>Give ARIA a clear goal in chat or by voice. Start with a small request,
        read her result, then add detail. You do not need tool names or special syntax.</p>
        <ol><li>Open <b>Settings → Connections</b> and configure your model provider.
        Restart ARIA after changing provider keys. Mail and calendar need their own connections.</li>
        <li>Choose the right project in <b>Work Center</b> before starting work.</li>
        <li>Say what you want, where to work, and what a finished result looks like.</li>
        <li>Check any approval request and review the result or saved file.</li></ol>
        <p><b>First try:</b> ask for a simple explanation, then a read-only calendar or
        file request once that connection is ready.</p>
        <p>This guide works offline. Examples are starting points: replace bracketed
        details before sending them in chat. Copying an example does not run it.</p>
        <p>Next: <a href="aria-guide:prompts">Write effective prompts</a> or
        <a href="aria-guide:work-center">Understand Work Center</a>.</p>
        """, (
            ('Check readiness', 'Tell me which model provider and tools are available right now. Report anything you cannot verify.'),
            ('First file request', 'Read the files in [folder] and list their names and types. Do not change anything.'),
        )),
    GuideTopic('prompts', 'Effective prompts', """
        <p>Use this pattern: <b>goal + context + boundaries + finished result</b>.</p>
        <ul><li><b>Goal:</b> the specific outcome you want.</li>
        <li><b>Context:</b> the project, files, dates, people, or sources to use.</li>
        <li><b>Boundaries:</b> what ARIA may change, a budget, and actions that need your review.</li>
        <li><b>Finished result:</b> a saved file, tested change, comparison table, or draft.</li></ul>
        <p>For example, “organize my stuff” leaves the destination and rules unclear.
        “List the PDFs in [folder], group them by topic, and propose names before moving
        anything” gives her a reviewable first step.</p>
        <p>For large work, ask for a plan and useful checkpoints. State actual paths and
        exact dates/time zones. When correcting a result, name what to change and what to keep.</p>
        <p>Choose <a href="aria-guide:tasks">a background task</a> for research or work
        that adapts as it goes; choose <a href="aria-guide:missions">a mission</a>
        for a saved, approved sequence of known steps.</p>
        """, (
            ('Reusable prompt template', 'Goal: [outcome]. Context: [project, files, sources]. Boundaries: [allowed changes and limits]. Deliver: [format and destination]. Verify: [how to check success]. Tell me if anything is blocked.'),
            ('Research with sources', 'Compare [option A] and [option B] for [my needs]. Use current primary sources, include links and dates, explain tradeoffs, and give me a comparison table. Do not buy or sign up for anything.'),
            ('Coding task', 'In [repository folder], fix [specific bug]. First inspect the code. Make the smallest complete fix, run the relevant checks, and report changed files and test results. Ask before committing or pushing.'),
        )),
    GuideTopic('work-center', 'Work Center', """
        <p><b>Work Center is ARIA’s activity and project panel.</b> Open it from the
        dashboard’s <b>Work Center</b> button. Give instructions in chat, then use this
        panel to inspect progress, outputs, costs, and connected computers.</p>
        <ul><li><b>Project settings:</b> choose or create a project at the top. Set its
        model budgets, preferred models, and file roots, then click <b>Save project settings</b>.</li>
        <li><b>Work:</b> inspect active executions and saved missions. Select a mission
        to see its steps and action receipts. Pause, resume, or cancel selected work.</li>
        <li><b>Deliverables:</b> find indexed files ARIA produced. Preview text, open a
        file, show its folder, or verify that the file still matches its recorded hash.</li>
        <li><b>Models and resources:</b> inspect model attempts and cost records, see
        CPU/memory/battery measurements, and choose full, balanced, or minimal.</li>
        <li><b>Computers:</b> see connected nodes and setup instructions for a second laptop.</li></ul>
        <p><b>A useful workflow:</b> choose the project → ask ARIA to do the work →
        check <b>Work</b> → open the result in <b>Deliverables</b>.</p>
        <p><b>Stop speech</b> only silences the voice. <b>Cancel foreground</b> requests
        cancellation of interactive work. Mission pause takes effect between steps;
        an action already running may finish after cancellation.</p>
        <p><b>Record checked receipt</b> is for you to attest to an outcome you checked
        yourself, such as confirming a sent message at its destination. Do not use it
        simply to clear a blocked status or repeat an uncertain action.</p>
        <p>More: <a href="aria-guide:projects">Projects</a>,
        <a href="aria-guide:tasks-vs-work-center">Tasks vs Work Center</a>,
        <a href="aria-guide:missions">Missions</a>,
        <a href="aria-guide:deliverables">Deliverables</a>,
        <a href="aria-guide:budgets">Budgets</a>.</p>
        """),
    GuideTopic('tasks-vs-work-center', 'Tasks vs Work Center', """
        <p><b>Tasks is a status feed. Work Center adds project management and controls.</b>
        They overlap, but they are not interchangeable.</p>
        <h3>The workspace’s Tasks tab</h3>
        <p>Shows delegated background tasks and their statuses, scheduled
        jobs, and Fleet activity such as tools currently running. Use it to answer
        “What is running or queued?” or “What is scheduled?”</p>
        <p>This view is a readout. Give instructions in chat to cancel or resume a
        delegated task, using its task ID. Ask for that task’s status in chat to get
        its full result or error.</p>
        <h3>Work Center</h3>
        <p>The <b>Work</b> tab shows active executions and saved missions for the
        selected project. Select work to inspect details, pause/resume a mission,
        request cancellation, or review action receipts.</p>
        <p>The other tabs add project settings, deliverables, model budgets and
        usage, resource profiles, and connected computers.</p>
        <p>Work Center currently does not replace the delegated-task and
        schedule lists in Tasks. A queued job may not appear in its active-work list
        until it starts running.</p>
        <p>Next: <a href="aria-guide:tasks">Background tasks</a>,
        <a href="aria-guide:missions">Durable missions</a>, or
        <a href="aria-guide:work-center">Work Center overview</a>.</p>
        """),
    GuideTopic('projects', 'Projects and memory', """
        <p>In <b>Work Center</b>, use the project selector or <b>New project</b> to keep
        unrelated work apart. Select a project <b>before</b> asking ARIA to start.</p>
        <p>Each project has its own conversation context, memories, missions, task
        journals, bookmark, generated files, deliverable records, and model usage.
        Personal keeps your existing workspace. Provider credentials, appearance,
        hardware settings, and reusable routines are shared.</p>
        <p>Switching projects does not move or cancel work already submitted. Running
        work keeps its original project; approved missions in other projects can continue.
        Switching projects ends an active Gemini Live session.</p>
        <p><b>File roots</b> in Project settings limit supported file tools to those
        absolute directories. Blank means unrestricted file tools. This does not
        sandbox shell commands or Python execution.</p>
        <p>Ask ARIA to remember a fact or preference, and inspect saved items in the
        dashboard’s <b>Memory</b> tab. A bookmark saves one conversation checkpoint per
        project; it does not pause a mission or restart work by itself.</p>
        """, (
            ('Remember a preference', 'Remember that for this project I want concise explanations and a list of saved file paths.'),
            ('Find a memory', 'Search your memory for [topic] and tell me what you found.'),
            ('Bookmark', 'Save this for later'),
            ('Return to a bookmark', 'Where were we'),
        )),
    GuideTopic('tasks', 'Chat and background tasks', """
        <p><b>Ordinary chat</b> is best for questions, quick actions, and interactive
        changes. Keep refining the request in the conversation.</p>
        <p><b>A delegated background task</b> lets ARIA plan and work while you continue
        using her. Explicitly ask for a delegated task, give an output destination,
        and ask her to return the <b>task ID</b>. Use the dashboard’s <b>Tasks</b> tab
        or ask for task status.</p>
        <p>Delegated tasks have bounded plans (up to five planned steps) and an execution
        time budget. They suit research, comparisons, and supported file work. They
        cannot use the shell-command or program-restart tools; use interactive work
        for changes that need shell testing.</p>
        <p>Keep ARIA running and the laptop awake for execution. A saved checkpoint can
        help resume interrupted delegated work; resuming creates a new task ID.
        Inspect partial results before retrying an action that may already have happened.</p>
        <p><a href="aria-guide:missions">Missions</a> store a more explicit, approved
        sequence. They are different from delegated tasks.</p>
        """, (
            ('Delegate research', 'Handle this as a delegated background task: compare three current primary sources on [topic]. Save a concise report with source links in [output folder]. Return the task ID and report any blockers.'),
            ('Check a task', 'Show the status and latest progress of task [task ID].'),
            ('Cancel a task', 'Cancel task [task ID].'),
            ('Resume a task', 'Resume task [task ID] from its saved checkpoint, and give me the new task ID.'),
        )),
    GuideTopic('missions', 'Durable missions', """
        <p>A <b>durable mission</b> is a saved, ordered plan of tool actions. Ask ARIA
        to create one, review its exact plan, approve it when ready, and keep its mission ID.</p>
        <ol><li>Specify known steps, allowed actions, limits, and the result you expect.</li>
        <li>Review the proposed plan and respond through the approval controls.</li>
        <li>Inspect the mission and step receipts under <b>Work Center → Work</b>.</li>
        <li>Pause/resume between steps or cancel it when you need to stop.</li></ol>
        <p>Use missions for predefined sequences. They do not automatically substitute
        one step’s output into the next or invent a branching workflow. Use chat or a
        background task for exploratory research and dependent steps.</p>
        <p>Progress survives restart, but ARIA must be running and the laptop awake
        to execute. Mission model spending defaults to <b>$0</b>; model-dependent steps
        need an appropriate approved cap and configured model prices.</p>
        <p><b>Waiting for approval</b> needs your decision. <b>Blocked</b> needs input,
        configuration, or evidence. <b>Unverified</b> means completion has not been
        established. Read the step details before retrying.</p>
        <p>Recorded effects should not repeat on resume. If an action’s outcome is
        uncertain, check the actual destination first. Only then use <b>Record checked
        receipt</b> if appropriate. A receipt records your check; it is not independent proof.</p>
        """, (
            ('Create a read-only mission', 'Create a durable mission with read-only steps to check tomorrow’s calendar and list my unread email. Show me the plan for approval and return the mission ID. Report missing connections instead of guessing.'),
            ('Inspect a mission', 'Show mission [mission ID], including its steps, current status, and anything blocking it.'),
            ('Pause a mission', 'Pause mission [mission ID].'),
            ('Resume a mission', 'Resume mission [mission ID].'),
            ('Cancel a mission', 'Cancel mission [mission ID].'),
        )),
    GuideTopic('deliverables', 'Deliverables and verification', """
        <p><b>Work Center → Deliverables</b> lists successful file-producing tool
        outputs that ARIA indexed. It is not a list of every file on your computer.</p>
        <ol><li>Select an output to preview supported text and see its recorded path.</li>
        <li>Click <b>Open file</b> to use the associated app, or <b>Show folder</b> to
        find it in your file manager.</li>
        <li>Click <b>Verify file</b> to check whether it still matches the recorded hash.</li></ol>
        <p>A matching hash confirms the file matches that saved record, not that the
        content is correct. A changed or missing file needs inspection. Version records
        retain hashes and metadata; they do <b>not</b> back up old file contents.</p>
        <p>Ask for an exact destination and a useful filename. When ARIA says she saved
        something, open it and check the content before relying on it.</p>
        """, (
            ('Request a saved result', 'Write a concise summary of [source file] and save it as [full output path]. Tell me the exact path and verify that the file exists.'),
        )),
    GuideTopic('budgets', 'Models and budgets', """
        <p>In <b>Work Center → Project settings</b>, set a daily model budget, per-task
        model budget, maximum output tokens, and preferred providers/models.
        Click <b>Save project settings</b>. Blank budget fields mean unlimited.</p>
        <p>Enter your provider’s actual <b>USD per million input/output tokens</b> in
        the model-rates JSON. Prices are owner-configured; ARIA does not fetch or
        guess your bill. The form’s numbers are examples, not current prices.</p>
        <p>Budgeted work refuses unpriced models. If a request is blocked, check the
        selected model, its rates, remaining allowance, and any separate mission cap.
        Daily limits use <b>UTC</b>.</p>
        <p><b>Models and resources</b> shows model attempts, latency, cost records,
        scope, and reasons. Reservations are conservative; missing provider usage
        stays estimated or unknown. These limits govern ARIA’s model calls, not
        purchases or all activity on an external provider account.</p>
        <p>Gemini Live is disabled for budgeted projects; the normal speech pipeline
        remains available. Provider keys are managed in Settings → Connections.</p>
        """),
    GuideTopic('resources', 'Performance profiles', """
        <p>Choose a profile under <b>Work Center → Models and resources</b>.</p>
        <ul><li><b>full:</b> normal visual update cadence and optional services.</li>
        <li><b>balanced:</b> fewer visual updates while idle or minimized.</li>
        <li><b>minimal:</b> reduced idle updates and fewer optional services at startup,
        including life-room supervision, face tracking, and loopback capture.</li></ul>
        <p>Start with balanced on a slower laptop. Try minimal if you need to reduce
        background work. Microphone and speech remain available.</p>
        <p>Restart ARIA for background-service changes to take effect. The panel shows
        measured CPU, memory, battery, and startup data; savings depend on your machine
        and workload.</p>
        """),
    GuideTopic('computers', 'Connect a second laptop', """
        <p><b>Work Center → Computers</b> shows actual connected nodes and the desktop
        companion setup command. Asking in chat does not install or pair the other laptop.</p>
        <ol><li>Set up the remote-control bridge and its TLS certificate.</li>
        <li>On the second laptop, install ARIA or the source and dependencies.</li>
        <li>Start the desktop companion with the bridge WSS URL, a distinct computer
        name, the shared folder root, and local pairing-key/certificate files.</li>
        <li>Check that its name appears in Computers, then use that exact name in requests.</li></ol>
        <p>The source entry point is <code>python -m aria.desktop_node</code>; the
        packaged entry point is <code>ARIA.exe --desktop-node</code>. The Computers
        tab has the full flag example. Source setup details live in
        <code>docs/DESKTOP-COMPANION.md</code>.</p>
        <p>By default it shares file reads inside the chosen root. <code>--allow-writes</code>
        enables writes; <code>--allow-controls</code> enables screen, keyboard, mouse,
        and web-link actions. Keep the pairing key in a local file, out of chat.</p>
        """, (
            ('Inspect the connection', 'List the connected computers and their available capabilities. Do not change anything.'),
            ('Read on another computer', 'On computer [exact connected name], list the files inside its shared root. Do not modify files or use screen controls.'),
        )),
    GuideTopic('routines', 'Reusable routines', """
        <p>A <b>routine</b> records a sequence of tool actions for reuse. It replays
        recorded actions; it does not plan a fresh goal like a delegated task.</p>
        <ol><li>Ask ARIA to start recording a named routine.</li>
        <li>Give the actions you want in separate messages.</li>
        <li>Ask her to stop recording and save it.</li>
        <li>Later, ask her to run that routine by name.</li></ol>
        <p>Review what you recorded before using it for actions that send, purchase,
        delete, or modify anything. Approval settings still apply. If a run partly
        succeeds, check the results before restarting the entire routine: replay may
        repeat actions. Routines are shared across projects.</p>
        """, (
            ('Start recording', 'Start recording a routine named work_setup.'),
            ('Save the recording', 'Stop recording and save the routine.'),
            ('Run it later', 'Run my work_setup routine.'),
        )),
    GuideTopic('commands', 'Commands and stopping work', """
        <p>Most requests use normal language. Open <b>Commands</b> with
        <b>Ctrl+K</b> to browse the tools loaded in your session. Choosing a prompt
        example drafts it; edit it before sending. <b>Ctrl+,</b> opens Settings.
        <b>Ctrl+Shift+V</b> deliberately attaches clipboard text.</p>
        <ul><li><b>Stop / quiet / stop talking:</b> interrupt speech.</li>
        <li><b>Cancel work:</b> request cancellation of foreground work.</li>
        <li><b>Cancel task [ID]:</b> cancel a delegated background task.</li>
        <li><b>Pause mission [ID] / Resume mission [ID] / Cancel mission [ID]:</b>
        control the selected durable mission through chat.</li></ul>
        <p>Use <b>Cancel mission</b> for missions. A phrase beginning “stop …” may be
        handled as a speech interruption. Cancellation cannot undo an action already
        completed; an in-flight action may still finish.</p>
        <p>Exact quick music phrases include <b>pause music</b>, <b>resume music</b>,
        <b>play music</b>, <b>next track</b>, <b>previous track</b>, <b>volume up</b>,
        and <b>volume down</b>. A compatible player must be available.</p>
        """, (
            ('Cancel interactive work', 'Cancel work'),
            ('Stop speech', 'Stop talking'),
            ('Pause playback', 'Pause music'),
        )),
    GuideTopic('connections', 'Connections and approvals', """
        <p><b>Settings → Connections</b> holds provider keys, Gmail credentials, and
        the calendar iCal URL. Blank secret fields keep existing values. Environment
        variables can manage some fields. Restart after changing provider keys.</p>
        <p>Gmail requires an app password; calendar access requires the private iCal
        URL. Without those connections, ask ARIA to report the missing setup rather
        than assume she can see your account.</p>
        <p><b>Settings → General</b> has approval mode:</p>
        <ul><li><b>auto:</b> permitted tool actions proceed automatically.</li>
        <li><b>confirm-risky:</b> actions classified as risky wait for confirmation.</li>
        <li><b>confirm-all:</b> tool actions wait for confirmation.</li></ul>
        <p>Use the actual <b>Confirm</b> control in the approval banner, Shell, or
        phone interface. Saying “yes” in chat does not resolve a pending approval.
        Change approval mode yourself in Settings. Safe mode blocks its restricted
        actions even when approval mode is auto.</p>
        <p>Some features depend on installed tools, permissions, or connected services.
        The <b>Providers</b> and <b>Extensions</b> tabs show the local configuration.</p>
        """, (
            ('Read mail', 'Show my unread emails. Do not send replies or change the messages.'),
            ('Check schedule', 'What is on my calendar tomorrow? Include the date and time zone.'),
            ('Set a reminder', 'Remind me on [date] at [time] [time zone] to [task]. Repeat the scheduled time back to me.'),
        )),
    GuideTopic('troubleshooting', 'When work gets stuck', """
        <ul><li><b>No answer:</b> check the configured provider, key, connection, and
        budget. Restart after a provider-key change.</li>
        <li><b>Waiting:</b> inspect Work Center → Work for the current mission step,
        required approval, missing input, or unverified outcome.</li>
        <li><b>Budget blocked:</b> check actual model rates, project limits, and the
        mission’s separate spending cap.</li>
        <li><b>File not found:</b> check the current project, exact path, file roots,
        and Deliverables. A record is not a backup.</li>
        <li><b>Second laptop missing:</b> check the companion is running, the bridge
        URL/certificate/key match, and the connected computer name.</li>
        <li><b>Slow interface:</b> try balanced or minimal, then restart for optional
        service changes.</li></ul>
        <p>Ask for the last completed step, exact error, and smallest next action.
        Avoid repeatedly telling ARIA to redo an uncertain action. Check its real
        destination before retrying or recording a receipt.</p>
        <p>To stop activity, use <a href="aria-guide:commands">the correct cancellation
        command</a>; silencing speech alone does not cancel work.</p>
        """, (
            ('Diagnose without repeating actions', 'Explain what is blocking [task or mission ID]. Show the last completed step, exact error, and safest next action. Do not retry actions yet.'),
        )),
)


class UserGuideWidget(QWidget):
    """Searchable topics with clipboard-only examples and internal navigation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('userGuide')
        self._topics = {topic.key: topic for topic in TOPICS}
        self._search_text = {topic.key: topic.search_text() for topic in TOPICS}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.search = QLineEdit()
        self.search.setAccessibleName('Search user guide')
        self.search.setPlaceholderText('Search the guide: missions, Work Center, prompts…')
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.topics = QListWidget()
        self.topics.setAccessibleName('Guide topics')
        self.topics.setMinimumWidth(130)
        self.topics.setWordWrap(True)
        self.browser = QTextBrowser()
        self.browser.setAccessibleName('Guide article')
        self.browser.setMinimumWidth(220)
        self.browser.setStyleSheet('QTextBrowser { font-size: 14px; }')
        self.browser.document().setDefaultStyleSheet(
            'a { color: #77e7ef; } p { margin-top: 8px; margin-bottom: 10px; } '
            'li { margin-bottom: 6px; } blockquote { margin-left: 12px; }')
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        split.addWidget(self.topics)
        split.addWidget(self.browser)
        split.setSizes([165, 440])
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        example_row = QHBoxLayout()
        self.examples = QComboBox()
        self.examples.setAccessibleName('Prompt example to copy')
        self.examples.setPlaceholderText('No prompt examples for this topic')
        self.examples.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.examples.setMinimumWidth(100)
        example_row.addWidget(self.examples, 1)
        self.copy = QPushButton('Copy prompt')
        self.copy.setToolTip('Copy to the clipboard. Edit and send it yourself in chat.')
        example_row.addWidget(self.copy)
        layout.addLayout(example_row)
        self.status = QLabel('Offline help. Copy an example, edit the details, then send it in chat.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.search.textChanged.connect(self._filter_topics)
        self.topics.currentItemChanged.connect(lambda current, previous: self._show_topic(current))
        self.browser.anchorClicked.connect(self._follow_link)
        self.copy.clicked.connect(self._copy_prompt)
        self._filter_topics()

    def _filter_topics(self):
        current = self.topics.currentItem()
        key = current.data(Qt.ItemDataRole.UserRole) if current else None
        # Match word prefixes, so searching UTC does not also match "outcome".
        terms = [re.compile(r'(?<!\w)' + re.escape(term))
                 for term in self.search.text().casefold().split()]
        self.topics.blockSignals(True)
        self.topics.clear()
        selected = 0
        for topic in TOPICS:
            if all(term.search(self._search_text[topic.key]) for term in terms):
                item = QListWidgetItem(topic.title)
                item.setData(Qt.ItemDataRole.UserRole, topic.key)
                self.topics.addItem(item)
                if topic.key == key:
                    selected = self.topics.count() - 1
        self.topics.setCurrentRow(selected if self.topics.count() else -1)
        self.topics.blockSignals(False)
        count = self.topics.count()
        self.summary.setText(f'{count} of {len(TOPICS)} topics' if count else
                             'No topics match. Clear the search to see all topics.')
        self._show_topic(self.topics.currentItem())

    def _show_topic(self, item):
        self.examples.clear()
        self.status.setText('Offline help. Copy an example, edit the details, then send it in chat.')
        if item is None:
            self.browser.setHtml('<h2>No topics found</h2><p>Try a shorter search or clear the search field.</p>')
        else:
            topic = self._topics[item.data(Qt.ItemDataRole.UserRole)]
            body = '<h2>' + escape(topic.title) + '</h2>' + topic.body
            if topic.examples:
                body += '<h3>Example prompts</h3>'
                for label, prompt in topic.examples:
                    self.examples.addItem(label, prompt)
                    self.examples.setItemData(self.examples.count() - 1, prompt, Qt.ItemDataRole.ToolTipRole)
                    body += '<p><b>' + escape(label) + '</b></p><blockquote>' + escape(prompt) + '</blockquote>'
            self.browser.setHtml(body)
        self.browser.verticalScrollBar().setValue(0)
        enabled = self.examples.count() > 0
        if enabled:
            self.examples.setCurrentIndex(0)
        self.examples.setEnabled(enabled)
        self.copy.setEnabled(enabled)

    def show_topic(self, key):
        """Navigate to a known topic, clearing filters only for valid destinations."""
        if key not in self._topics:
            return False
        self.search.clear()
        for index in range(self.topics.count()):
            item = self.topics.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.topics.setCurrentItem(item)
                return True
        return False

    def _follow_link(self, url):
        if url.scheme() == 'aria-guide':
            self.show_topic(url.path())

    def _copy_prompt(self):
        prompt = self.examples.currentData()
        if prompt:
            QApplication.clipboard().setText(prompt)
            self.status.setText('Prompt copied. Replace bracketed details, then paste it into chat and send when ready.')
