import AppKit

@MainActor final class DiscoverSheetController: NSWindowController, NSTableViewDelegate, NSTableViewDataSource {
    weak var library: LibraryWindowController?
    let table = NSTableView(), scope = NSTextField(wrappingLabelWithString: ""), status = NSTextField(wrappingLabelWithString: "")
    let importButton = NSButton(), refreshButton = NSButton(), chooseButton = NSButton()
    var candidates: [JSON] = [], selected: Set<String> = [], roots: [String]?
    init(library: LibraryWindowController) {
        self.library = library
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 860, height: 620), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: true)
        panel.title = "发现已有 HTML"; panel.isReleasedWhenClosed = false
        super.init(window: panel)
        let check = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("check")); check.width = 34; check.minWidth = 34; check.maxWidth = 34
        let title = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("candidate")); title.width = 760
        table.addTableColumn(check); table.addTableColumn(title); table.headerView = nil; table.rowHeight = 65; table.dataSource = self; table.delegate = self
        let scroll = NSScrollView(); scroll.documentView = table; scroll.hasVerticalScroller = true
        scope.font = .systemFont(ofSize: 12); scope.textColor = .secondaryLabelColor
        status.font = .systemFont(ofSize: 12); status.textColor = .secondaryLabelColor
        scope.stringValue = "默认读取 Documents/Lumen 中的 HTML；可选择自己的目录，不扫描整个电脑。"
        chooseButton.title = "选择其他目录…"; chooseButton.target = self; chooseButton.action = #selector(chooseRoot); chooseButton.bezelStyle = .rounded
        refreshButton.title = "重新发现"; refreshButton.target = self; refreshButton.action = #selector(refreshClicked); refreshButton.bezelStyle = .rounded
        let cancel = NSButton(title: "关闭", target: self, action: #selector(closeSheet)); cancel.bezelStyle = .rounded; cancel.keyEquivalent = "\u{1b}"
        importButton.title = "导入所选"; importButton.target = self; importButton.action = #selector(importSelected); importButton.bezelStyle = .rounded
        let actions = NSStackView(views: [chooseButton, refreshButton, cancel, importButton]); actions.spacing = 8
        let stack = NSStackView(views: [scope, scroll, status, actions]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false; panel.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: panel.contentView!.leadingAnchor, constant: 20), stack.trailingAnchor.constraint(equalTo: panel.contentView!.trailingAnchor, constant: -20), stack.topAnchor.constraint(equalTo: panel.contentView!.topAnchor, constant: 20), stack.bottomAnchor.constraint(equalTo: panel.contentView!.bottomAnchor, constant: -20), scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 390)] + [scope, scroll, status].map { $0.widthAnchor.constraint(equalTo: stack.widthAnchor) })
    }
    required init?(coder: NSCoder) { fatalError() }
    func numberOfRows(in tableView: NSTableView) -> Int { candidates.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let candidate = candidates[row], path = candidate["path"] as? String ?? ""
        if tableColumn?.identifier.rawValue == "check" {
            let checkbox = NSButton(checkboxWithTitle: "", target: self, action: #selector(toggle(_:))); checkbox.tag = row
            checkbox.state = selected.contains(path) ? .on : .off; checkbox.isEnabled = candidate["registered"] as? Bool != true; return checkbox
        }
        let cell = DocumentCell(frame: .zero)
        cell.title.stringValue = (candidate["registered"] as? Bool == true ? "已收录 · " : "") + (candidate["title"] as? String ?? URL(fileURLWithPath: path).lastPathComponent)
        cell.detail.stringValue = path; cell.toolTip = path; return cell
    }
    @objc func toggle(_ sender: NSButton) {
        guard candidates.indices.contains(sender.tag), let path = candidates[sender.tag]["path"] as? String else { return }
        if sender.state == .on { selected.insert(path) } else { selected.remove(path) }
        status.stringValue = "已选择 \(selected.count) 份成果"; importButton.isEnabled = !selected.isEmpty
    }
    func discover() async {
        guard let library else { return }
        refreshButton.isEnabled = false; chooseButton.isEnabled = false; status.stringValue = "正在读取所选范围的成果入口…"
        defer { refreshButton.isEnabled = true; chooseButton.isEnabled = true }
        do {
            var params: JSON = ["limit": 150]; if let roots { params["roots"] = roots }
            let result = try await library.engine.call("discover", params)
            candidates = result["candidates"] as? [JSON] ?? []; selected = []; table.reloadData(); importButton.isEnabled = false
            if let scopes = result["scope"] as? [String] { scope.stringValue = "当前发现范围：" + scopes.joined(separator: "；") }
            status.stringValue = "找到 \(candidates.count) 份成果入口" + (result["truncated"] as? Bool == true ? "；更多成果请缩小目录范围。" : "，勾选需要收录的知识。")
        } catch { status.stringValue = error.localizedDescription }
    }
    @objc func refreshClicked() { Task { await discover() } }
    @objc func chooseRoot() {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true
        panel.beginSheetModal(for: window!) { [weak self] response in
            guard response == .OK, let path = panel.url?.path, let self else { return }
            self.roots = [path]; self.scope.stringValue = "当前发现范围：" + path; Task { await self.discover() }
        }
    }
    @objc func closeSheet() { library?.window?.endSheet(window!); window?.orderOut(nil) }
    @objc func importSelected() { Task { await importPaths() } }
    func importPaths() async {
        guard let library, !selected.isEmpty else { return }
        let paths = selected.sorted(); importButton.isEnabled = false; refreshButton.isEnabled = false; chooseButton.isEnabled = false
        var completed = 0, errors: [String] = [], lastID: String?
        for path in paths {
            status.stringValue = "正在导入 \(completed + errors.count + 1)/\(paths.count)：\(URL(fileURLWithPath: path).lastPathComponent)"
            do {
                let result = try await library.engine.call(path.lowercased().hasSuffix(".pptx") ? "convert" : "import", path.lowercased().hasSuffix(".pptx") ? ["source": path, "kind": "ppt"] : ["path": path, "include_test": false])
                lastID = (result["document"] as? JSON)?["id"] as? String; completed += 1; selected.remove(path)
            } catch { errors.append(URL(fileURLWithPath: path).lastPathComponent + "：" + error.localizedDescription) }
        }
        await library.refresh(); if let lastID { await library.open(documentID: lastID) }
        status.stringValue = "已导入 \(completed) 份成果" + (errors.isEmpty ? "。" : "；\(errors.count) 份未完成：\n" + errors.joined(separator: "\n"))
        status.textColor = errors.isEmpty ? .secondaryLabelColor : .systemOrange
        importButton.isEnabled = !selected.isEmpty; refreshButton.isEnabled = true; chooseButton.isEnabled = true
        if errors.isEmpty { await discover(); status.stringValue = "已导入 \(completed) 份成果，可关闭此页继续阅读。" }
    }
}
