import AppKit

@MainActor final class TaskWindowController: NSWindowController, NSTableViewDataSource, NSTableViewDelegate, NSWindowDelegate {
    weak var library: LibraryWindowController?
    let table = NSTableView(), detail = NSTextField(wrappingLabelWithString: ""), status = NSTextField(labelWithString: "")
    let cancelButton = NSButton(), resumeButton = NSButton(), openButton = NSButton()
    var tasks: [JSON] = []
    private var timer: Timer?, refreshing = false
    init(library: LibraryWindowController) {
        self.library = library
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 780, height: 530), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: true)
        window.title = "Lumen · 生成任务"; window.isReleasedWhenClosed = false
        super.init(window: window); window.delegate = self
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("tasks")); table.addTableColumn(column); table.headerView = nil
        table.delegate = self; table.dataSource = self; table.rowHeight = 62
        let scroll = NSScrollView(); scroll.documentView = table; scroll.hasVerticalScroller = true
        detail.font = .systemFont(ofSize: 12); detail.textColor = .secondaryLabelColor
        for (button, title, action) in [(cancelButton, "取消任务", #selector(cancelTask)), (resumeButton, "接续任务", #selector(resumeTask)), (openButton, "打开生成成果", #selector(openResult))] { button.title = title; button.bezelStyle = .rounded; button.target = self; button.action = action }
        let refresh = NSButton(title: "刷新", target: self, action: #selector(refreshTasks)); refresh.bezelStyle = .rounded
        let actions = NSStackView(views: [cancelButton, resumeButton, openButton, refresh]); actions.spacing = 8
        let note = NSTextField(wrappingLabelWithString: "关闭知识库窗口不会停止生成。取消只处理选中的任务；接续保留已有输入与已完成步骤。")
        note.font = .systemFont(ofSize: 12); note.textColor = .secondaryLabelColor
        let stack = NSStackView(views: [note, scroll, detail, actions, status]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false; window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 20), stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -20), stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 20), stack.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor, constant: -20), scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 260)] + [note, scroll, detail].map { $0.widthAnchor.constraint(equalTo: stack.widthAnchor) })
        status.font = .systemFont(ofSize: 11); status.textColor = .secondaryLabelColor; updateActions(); window.center()
    }
    required init?(coder: NSCoder) { fatalError() }
    override func showWindow(_ sender: Any?) {
        super.showWindow(sender); Task { await refresh() }
        timer?.invalidate(); timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.window?.isVisible == true, self.tasks.contains(where: { ["queued", "running", "cancelling"].contains($0["status"] as? String ?? "") }) else { return }
                await self.refresh()
            }
        }
    }
    func windowWillClose(_ notification: Notification) { timer?.invalidate(); timer = nil }
    func numberOfRows(in tableView: NSTableView) -> Int { tasks.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let cell = DocumentCell(frame: .zero), task = tasks[row], params = task["params"] as? JSON ?? [:]
        cell.title.stringValue = params["request"] as? String ?? task["id"] as? String ?? "生成任务"
        let state = task["status"] as? String ?? "unknown"
        let labels = ["queued": "等待执行", "running": "生成中", "cancelling": "正在取消", "succeeded": "已完成", "failed": "失败", "cancelled": "已取消", "interrupted": "中断"]
        cell.detail.stringValue = (labels[state] ?? state) + " · " + (task["step"] as? String ?? "") + " · " + (params["source"] as? String ?? "")
        return cell
    }
    func tableViewSelectionDidChange(_ notification: Notification) { updateActions() }
    var selected: JSON? { tasks.indices.contains(table.selectedRow) ? tasks[table.selectedRow] : nil }
    private func updateActions() {
        let task = selected ?? [:], state = task["status"] as? String ?? ""
        cancelButton.isEnabled = ["queued", "running"].contains(state)
        resumeButton.isEnabled = task["can_resume"] as? Bool == true
        openButton.isEnabled = state == "succeeded" && task["document_id"] is String
        detail.stringValue = (task["error"] as? JSON)?["message"] as? String ?? (task["message"] as? String ?? "选择任务查看状态、打开成果或接续。")
    }
    func refresh() async {
        guard !refreshing, let library else { return }; refreshing = true
        defer { refreshing = false }
        do {
            let id = selected?["id"] as? String
            let result = try await library.engine.call("tasks"); tasks = result["tasks"] as? [JSON] ?? []; table.reloadData()
            if let id, let index = tasks.firstIndex(where: { $0["id"] as? String == id }) { table.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false) }
            updateActions(); status.stringValue = "\(tasks.count) 个任务 · 本机持久记录"
        } catch { status.stringValue = error.localizedDescription }
    }
    @objc func refreshTasks() { Task { await refresh() } }
    @objc func cancelTask() { run("cancel") }
    @objc func resumeTask() { run("resume") }
    private func run(_ action: String) {
        guard let id = selected?["id"] as? String, let library else { return }
        Task { do { _ = try await library.engine.call(action, ["task_id": id]); await refresh(); await library.resumeTaskObservation() } catch { status.stringValue = error.localizedDescription } }
    }
    @objc func openResult() {
        guard let id = selected?["document_id"] as? String, let library else { return }
        Task { await library.open(documentID: id); library.showWindow(nil); library.window?.makeKeyAndOrderFront(nil) }
    }
}
