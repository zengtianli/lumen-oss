import AppKit
import WebKit

final class DocumentCell: NSTableCellView {
    let title = NSTextField(labelWithString: ""), detail = NSTextField(labelWithString: "")
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        title.font = .systemFont(ofSize: 13, weight: .medium); title.lineBreakMode = .byTruncatingTail
        detail.font = .systemFont(ofSize: 11); detail.textColor = .secondaryLabelColor; detail.lineBreakMode = .byTruncatingTail
        let stack = NSStackView(views: [title, detail]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 5
        stack.translatesAutoresizingMaskIntoConstraints = false; addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12), stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12), stack.centerYAnchor.constraint(equalTo: centerYAnchor)])
        textField = title
    }
    required init?(coder: NSCoder) { fatalError() }
}

private struct MetadataDraft: Codable, Equatable {
    var title: String?, project: String?, tags: [String]?, notes: String?
    var revision: Int
    var baseTitle: String, baseProject: String, baseTags: [String], baseNotes: String
    var isEmpty: Bool { title == nil && project == nil && tags == nil && notes == nil }
    func matchesBase(_ document: KnowledgeDocument) -> Bool {
        baseTitle == document.title && baseProject == document.project && baseTags == document.tags && baseNotes == document.notes
    }
    mutating func rebase(_ document: KnowledgeDocument) {
        revision = document.metadataRevision; baseTitle = document.title; baseProject = document.project; baseTags = document.tags; baseNotes = document.notes
    }
}

final class LibraryWindowController: NSWindowController, NSWindowDelegate, NSTableViewDelegate, NSTableViewDataSource, NSSearchFieldDelegate, NSTextViewDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    let engine: EngineClient
    let sidebar = NSTableView(), documentsTable = NSTableView(), searchField = NSSearchField()
    let projectPopup = NSPopUpButton(), tagPopup = NSPopUpButton(), pagePopup = NSPopUpButton()
    let reader: WKWebView
    let status = NSTextField(labelWithString: ""), countLabel = NSTextField(labelWithString: "")
    let progress = NSProgressIndicator(), favoriteButton = NSButton(), inspectorButton = NSButton()
    private var generateButton = NSButton(), importButton = NSButton(), saveButton = NSButton()
    var generationLabel: String { generateButton.title }
    let notes = NSTextView(), titleField = NSTextField(), projectField = NSTextField(), tagsField = NSTextField()
    let versionPopup = NSPopUpButton(), versionLabel = NSTextField(labelWithString: "")
    let sourceLabel = NSTextField(wrappingLabelWithString: "")
    let inspector = NSStackView(), split = NSSplitView()
    var documents: [KnowledgeDocument] = [], matches: [SearchMatch] = [], allDocuments: [KnowledgeDocument] = []
    var selectedDocument: KnowledgeDocument?, pages: [KnowledgePage] = [], selectedPage: KnowledgePage?
    private var homePageID: String?
    var versions: [JSON] = []
    private var sidebarIndex = 0, queryGeneration = 0, openingGeneration = 0
    private var observingTaskID: String?, taskTimer: Timer?, searchTimer: Timer?
    private var pendingScroll: Double?, pendingAnchor: String?, notesDirty = false, bindingPage = false
    private let sidebarNames = ["全部知识", "收藏", "最近阅读", "门户", "阅读与报告", "教学", "已归档"]
    private let sidebarSymbols = ["books.vertical", "star", "clock", "rectangle.3.group", "doc.text", "graduationcap", "archivebox"]
    private var sidebarContainer = NSView(), listContainer = NSView(), readerContainer = NSView()
    private var readingWidth: NSLayoutConstraint?
    private var generateSheet: GenerateSheetController?
    private var readyAnnounced = false
    private var libraryRefreshed = false
    private var taskWindow: TaskWindowController?
    private var discoverSheet: DiscoverSheetController?
    private var guideWindow: NSWindowController?
    private var metadataBaseline: KnowledgeDocument?
    private var metadataDrafts: [String: MetadataDraft] = [:]
    private var pendingDraftWrites: Set<String> = []
    private var bindingMetadata = false

    init(engine: EngineClient) {
        self.engine = engine
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        reader = WKWebView(frame: .zero, configuration: configuration)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: true)
        window.title = LumenPaths.displayName; window.minSize = NSSize(width: 950, height: 620)
        window.titleVisibility = .visible; window.titlebarAppearsTransparent = false
        window.isReleasedWhenClosed = false; window.isMovable = true; window.isMovableByWindowBackground = false
        super.init(window: window); window.delegate = self
        // Test and measurement processes must not restore or rewrite the user's
        // real window geometry; the normal client still remembers its frame.
        if ProcessInfo.processInfo.environment["LUMEN_DEFAULTS_DOMAIN"] == nil {
            window.setFrameAutosaveName("Lumen.library")
        }
        reader.navigationDelegate = self
        configuration.userContentController.add(self, name: "lumenSave")
        configuration.userContentController.add(self, name: "lumenPosition")
        makeInterface(); window.center()
    }
    required init?(coder: NSCoder) { fatalError() }
    deinit { taskTimer?.invalidate(); searchTimer?.invalidate() }

    private func button(_ title: String, symbol: String, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.bezelStyle = .rounded; button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: title); button.imagePosition = .imageLeading
        return button
    }
    private func scroll(_ view: NSView) -> NSScrollView {
        let scroll = NSScrollView(); scroll.documentView = view; scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true; scroll.borderType = .noBorder
        return scroll
    }
    private func stack(_ views: [NSView], vertical: Bool = true, spacing: CGFloat = 10) -> NSStackView {
        let result = NSStackView(views: views); result.orientation = vertical ? .vertical : .horizontal
        result.alignment = vertical ? .leading : .centerY; result.spacing = spacing; return result
    }
    private func fill(_ view: NSView, in parent: NSView, inset: CGFloat = 0) {
        view.translatesAutoresizingMaskIntoConstraints = false; parent.addSubview(view)
        NSLayoutConstraint.activate([view.leadingAnchor.constraint(equalTo: parent.leadingAnchor, constant: inset), view.trailingAnchor.constraint(equalTo: parent.trailingAnchor, constant: -inset), view.topAnchor.constraint(equalTo: parent.topAnchor, constant: inset), view.bottomAnchor.constraint(equalTo: parent.bottomAnchor, constant: -inset)])
    }
    func makeInterface() {
        guard let content = window?.contentView else { return }
        content.wantsLayer = true
        let generate = button("生成知识", symbol: "sparkles", action: #selector(generateKnowledge)); generateButton = generate
        importButton = button("导入 HTML", symbol: "square.and.arrow.down", action: #selector(importKnowledge))
        let refresh = button("", symbol: "arrow.clockwise", action: #selector(refreshKnowledge)); refresh.toolTip = "刷新知识库 ⌘R"
        let spacer = NSView(); spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        countLabel.font = .systemFont(ofSize: 11); countLabel.textColor = .secondaryLabelColor
        let tasks = button("生成任务", symbol: "list.bullet.rectangle", action: #selector(showTasks))
        let discover = button("发现已有 HTML", symbol: "magnifyingglass.circle", action: #selector(showDiscoverSheet))
        let tools = stack([generate, importButton, discover, tasks, spacer, countLabel, refresh], vertical: false, spacing: 10)
        tools.edgeInsets = NSEdgeInsets(top: 10, left: 14, bottom: 10, right: 14)
        split.isVertical = true; split.dividerStyle = .thin
        let root = stack([tools, split], spacing: 0); root.alignment = .leading
        fill(root, in: content)
        tools.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true; split.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        let sideColumn = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("navigation")); sidebar.addTableColumn(sideColumn)
        sidebar.headerView = nil; sidebar.rowHeight = 38; sidebar.delegate = self; sidebar.dataSource = self; sidebar.style = .sourceList
        sidebar.backgroundColor = .windowBackgroundColor; sidebar.focusRingType = .none
        let sidebarTitle = NSTextField(labelWithString: "知识库"); sidebarTitle.font = .systemFont(ofSize: 11, weight: .semibold); sidebarTitle.textColor = .secondaryLabelColor
        let sideStack = stack([sidebarTitle, scroll(sidebar)], spacing: 12); sideStack.edgeInsets = NSEdgeInsets(top: 18, left: 8, bottom: 12, right: 8)
        fill(sideStack, in: sidebarContainer)
        sideStack.arrangedSubviews.last?.widthAnchor.constraint(equalTo: sideStack.widthAnchor, constant: -16).isActive = true
        sidebarContainer.widthAnchor.constraint(greaterThanOrEqualToConstant: 155).isActive = true
        sidebarContainer.widthAnchor.constraint(lessThanOrEqualToConstant: 220).isActive = true

        searchField.placeholderString = "全文搜索知识"; searchField.delegate = self; searchField.sendsSearchStringImmediately = true
        searchField.target = self; searchField.action = #selector(searchChanged)
        projectPopup.target = self; projectPopup.action = #selector(filtersChanged); projectPopup.addItem(withTitle: "所有项目")
        tagPopup.target = self; tagPopup.action = #selector(filtersChanged); tagPopup.addItem(withTitle: "所有主题")
        let filters = stack([projectPopup, tagPopup], vertical: false, spacing: 6); filters.distribution = .fillEqually
        let docColumn = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("documents")); documentsTable.addTableColumn(docColumn)
        documentsTable.headerView = nil; documentsTable.rowHeight = 65; documentsTable.delegate = self; documentsTable.dataSource = self
        documentsTable.focusRingType = .none; documentsTable.usesAlternatingRowBackgroundColors = false
        let listStack = stack([searchField, filters, scroll(documentsTable)], spacing: 9); listStack.edgeInsets = NSEdgeInsets(top: 14, left: 10, bottom: 0, right: 10)
        fill(listStack, in: listContainer)
        for view in listStack.arrangedSubviews { view.widthAnchor.constraint(equalTo: listStack.widthAnchor, constant: -20).isActive = true }
        listContainer.widthAnchor.constraint(greaterThanOrEqualToConstant: 245).isActive = true; listContainer.widthAnchor.constraint(lessThanOrEqualToConstant: 340).isActive = true

        pagePopup.target = self; pagePopup.action = #selector(pageChanged); pagePopup.addItem(withTitle: "选择知识开始阅读")
        pagePopup.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let back = button("", symbol: "chevron.left", action: #selector(goBack)); back.toolTip = "返回上一页 ⌘["
        let edit = button("编辑", symbol: "pencil", action: #selector(editPage))
        let save = button("保存", symbol: "checkmark", action: #selector(savePage)); saveButton = save; save.toolTip = "保存页面与批注 ⌘S"
        favoriteButton.title = ""; favoriteButton.target = self; favoriteButton.action = #selector(toggleFavorite); favoriteButton.bezelStyle = .rounded
        inspectorButton.title = ""; inspectorButton.target = self; inspectorButton.action = #selector(toggleInspector); inspectorButton.bezelStyle = .rounded
        inspectorButton.image = NSImage(systemSymbolName: "sidebar.trailing", accessibilityDescription: "笔记与版本")
        let readerTools = stack([back, pagePopup, edit, save, favoriteButton, inspectorButton], vertical: false, spacing: 7)
        readerTools.edgeInsets = NSEdgeInsets(top: 10, left: 12, bottom: 10, right: 12)
        progress.style = .spinning; progress.controlSize = .small; progress.isDisplayedWhenStopped = false
        status.font = .systemFont(ofSize: 11); status.textColor = .secondaryLabelColor; status.lineBreakMode = .byTruncatingTail
        let footer = stack([progress, status], vertical: false, spacing: 8); footer.edgeInsets = NSEdgeInsets(top: 7, left: 12, bottom: 8, right: 12)
        let reading = stack([readerTools, reader, footer], spacing: 0)
        for view in reading.arrangedSubviews { view.widthAnchor.constraint(equalTo: reading.widthAnchor).isActive = true }
        let readerAndInspector = stack([reading, inspector], vertical: false, spacing: 0); readerAndInspector.alignment = .top
        fill(readerAndInspector, in: readerContainer)
        reading.heightAnchor.constraint(equalTo: readerAndInspector.heightAnchor).isActive = true
        inspector.heightAnchor.constraint(equalTo: readerAndInspector.heightAnchor).isActive = true
        inspector.widthAnchor.constraint(equalToConstant: 260).isActive = true
        makeInspector(); inspector.isHidden = !LumenPaths.defaults.bool(forKey: "reading.inspector")
        readingWidth = reading.widthAnchor.constraint(equalTo: readerAndInspector.widthAnchor, constant: inspector.isHidden ? 0 : -260)
        readingWidth?.isActive = true
        split.addArrangedSubview(sidebarContainer); split.addArrangedSubview(listContainer); split.addArrangedSubview(readerContainer)
        split.setHoldingPriority(.defaultHigh, forSubviewAt: 0); split.setHoldingPriority(.defaultHigh, forSubviewAt: 1); split.setHoldingPriority(.defaultLow, forSubviewAt: 2)
        sidebarIndex = min(6, max(0, LumenPaths.defaults.integer(forKey: "library.sidebar")))
        sidebar.selectRowIndexes(IndexSet(integer: sidebarIndex), byExtendingSelection: false)
        updateDocumentUI()
        reader.loadHTMLString("<!doctype html><html lang='zh'><meta charset='utf-8'><style>body{font:16px -apple-system;padding:72px;color:#3d5264;background:#fbfcfe}h1{font-size:30px;color:#183a4b}p{line-height:1.8;max-width:560px}</style><h1>让成果成为知识</h1><p>从左侧打开 HTML 门户、报告或教学页面。也可以生成新的知识，或导入已经完成的成果。</p><p>阅读、编辑与个人笔记保存在同一知识库，agent 可以通过 CLI、API 和 MCP 接着使用。</p></html>", baseURL: nil)
    }

    private func makeInspector() {
        inspector.orientation = .vertical; inspector.alignment = .leading; inspector.spacing = 10
        inspector.edgeInsets = NSEdgeInsets(top: 18, left: 16, bottom: 16, right: 16)
        let header = NSTextField(labelWithString: "笔记与版本"); header.font = .systemFont(ofSize: 15, weight: .semibold)
        titleField.placeholderString = "成果标题"; projectField.placeholderString = "所属项目"; tagsField.placeholderString = "主题标签，以逗号分隔"
        for field in [titleField, projectField, tagsField] { field.delegate = self }
        versionLabel.font = .systemFont(ofSize: 11); versionLabel.textColor = .secondaryLabelColor
        sourceLabel.font = .systemFont(ofSize: 11); sourceLabel.textColor = .secondaryLabelColor; sourceLabel.isSelectable = true
        let reveal = button("查看原始来源", symbol: "folder", action: #selector(revealSource))
        notes.font = .systemFont(ofSize: 13); notes.isRichText = false; notes.textContainerInset = NSSize(width: 8, height: 8); notes.delegate = self
        notes.autoresizingMask = [.width]; notes.isVerticallyResizable = true; notes.textContainer?.widthTracksTextView = true
        let noteScroll = scroll(notes); noteScroll.borderType = .bezelBorder; noteScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 120).isActive = true
        let save = button("保存笔记与信息", symbol: "checkmark", action: #selector(saveMetadata))
        let savedInfo = button("查看已保存信息", symbol: "arrow.clockwise", action: #selector(reviewSavedMetadata))
        versionPopup.target = self; versionPopup.action = #selector(versionChoiceChanged)
        let compare = button("比较版本", symbol: "square.split.2x1", action: #selector(compareVersion))
        let restore = button("恢复所选版本", symbol: "clock.arrow.circlepath", action: #selector(restoreVersion))
        let archive = button("归档 / 恢复收录", symbol: "archivebox", action: #selector(toggleArchive))
        for view in [header, titleField, projectField, tagsField, versionLabel, sourceLabel, reveal, NSTextField(labelWithString: "个人笔记"), noteScroll, save, savedInfo, NSTextField(labelWithString: "历史版本"), versionPopup, compare, restore, archive] { inspector.addArrangedSubview(view); view.widthAnchor.constraint(equalTo: inspector.widthAnchor, constant: -32).isActive = true }
        let spacer = NSView(); inspector.addArrangedSubview(spacer); spacer.setContentHuggingPriority(.defaultLow, for: .vertical)
    }

    func numberOfRows(in tableView: NSTableView) -> Int { tableView === sidebar ? sidebarNames.count : matches.isEmpty ? documents.count : matches.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        if tableView === sidebar {
            let cell = NSTableCellView(); let label = NSTextField(labelWithString: sidebarNames[row]); label.font = .systemFont(ofSize: 13)
            let icon = NSImageView(image: NSImage(systemSymbolName: sidebarSymbols[row], accessibilityDescription: sidebarNames[row]) ?? NSImage())
            icon.contentTintColor = .secondaryLabelColor; icon.widthAnchor.constraint(equalToConstant: 18).isActive = true
            let line = stack([icon, label], vertical: false, spacing: 9); fill(line, in: cell, inset: 6); cell.textField = label; return cell
        }
        let cell = DocumentCell(frame: .zero)
        if !matches.isEmpty, matches.indices.contains(row) {
            let match = matches[row]; cell.title.stringValue = match.pageTitle.isEmpty ? match.title : match.pageTitle
            cell.detail.stringValue = match.title + " · " + match.snippet; cell.toolTip = match.snippet
        } else if documents.indices.contains(row) {
            let document = documents[row]; cell.title.stringValue = (document.favorite ? "★ " : "") + document.title
            cell.detail.stringValue = [document.kindTitle, document.project, "\(document.pageCount) 页 · v\(document.version)"].filter { !$0.isEmpty }.joined(separator: " · ")
            cell.toolTip = document.title
        }
        return cell
    }
    func tableViewSelectionDidChange(_ notification: Notification) {
        guard let table = notification.object as? NSTableView else { return }
        if table === sidebar {
            sidebarIndex = max(0, sidebar.selectedRow); LumenPaths.defaults.set(sidebarIndex, forKey: "library.sidebar")
            Task { await refresh() }
        } else if table === documentsTable, documentsTable.selectedRow >= 0 {
            let index = documentsTable.selectedRow
            if !matches.isEmpty, matches.indices.contains(index) {
                let match = matches[index]; Task { await open(documentID: match.documentID, pageID: match.pageID, anchor: match.anchor) }
            } else if documents.indices.contains(index) {
                let document = documents[index]; Task { await open(documentID: document.id) }
            }
        }
    }
    @objc func searchChanged() {
        searchTimer?.invalidate(); searchTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: false) { [weak self] _ in Task { await self?.refresh() } }
    }
    @objc func filtersChanged() { Task { await refresh() } }
    @objc func refreshKnowledge() { Task { await refresh() } }
    @objc func generateKnowledge() { showGenerateSheet() }
    @objc func importKnowledge() { chooseImport() }
    @objc func showTasks() {
        if taskWindow == nil { taskWindow = TaskWindowController(library: self) }
        taskWindow?.showWindow(nil); taskWindow?.window?.makeKeyAndOrderFront(nil)
    }
    @objc func showDiscoverSheet() {
        let sheet = discoverSheet ?? DiscoverSheetController(library: self); discoverSheet = sheet
        window?.beginSheet(sheet.window!, completionHandler: nil); Task { await sheet.discover() }
    }
    func showGuide() {
        Task {
            do {
                let result = try await engine.call("guide", ["topic": "overview"])
                let body = result["body"] as? String ?? result["text"] as? String ?? ""
                let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: 660), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: true)
                window.title = "Lumen · 生成标准"; window.isReleasedWhenClosed = false
                let text = NSTextView(); text.isEditable = false; text.textContainerInset = NSSize(width: 24, height: 24); text.font = .systemFont(ofSize: 15)
                if let rich = try? AttributedString(markdown: body) { text.textStorage?.setAttributedString(NSAttributedString(rich)); text.textStorage?.addAttribute(.font, value: NSFont.systemFont(ofSize: 15), range: NSRange(location: 0, length: text.string.utf16.count)) }
                else { text.string = body }
                if text.string.isEmpty { text.string = "生成标准尚未返回，请通过 lumen guide 查看当前产品规则。" }
                fill(scroll(text), in: window.contentView!)
                guideWindow = NSWindowController(window: window)
                if NSApp.activationPolicy() != .prohibited { window.center(); guideWindow?.showWindow(nil) }
            } catch { showError(error) }
        }
    }
    @objc func pageChanged() {
        guard pages.indices.contains(pagePopup.indexOfSelectedItem) else { return }
        let page = pages[pagePopup.indexOfSelectedItem]; Task { await loadPage(page, scroll: 0, anchor: nil) }
    }
    @objc func versionChoiceChanged() { }
    @objc func goBack() { reader.goBack() }
    @objc func editPage() { reader.evaluateJavaScript("window.LumenReader && window.LumenReader.setEditing(true)") }
    @objc func savePage() {
        guard selectedDocument != nil else { return }
        reader.evaluateJavaScript("window.LumenReader && window.LumenReader.save()") { [weak self] _, error in if let error { self?.showError(error) } }
    }
    @objc func toggleInspector() {
        setInspectorVisible(inspector.isHidden); LumenPaths.defaults.set(!inspector.isHidden, forKey: "reading.inspector")
        if !inspector.isHidden { Task { await loadVersions() } }
    }
    func setInspectorVisible(_ visible: Bool) {
        inspector.isHidden = !visible; readingWidth?.constant = visible ? -260 : 0
        window?.contentView?.layoutSubtreeIfNeeded()
    }
    func focusSearch() { window?.makeFirstResponder(searchField); searchField.selectText(nil) }

    func refresh() async {
        queryGeneration += 1; let ticket = queryGeneration
        let query = searchField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        var params: JSON = ["archived": sidebarIndex == 6]
        if sidebarIndex == 1 { params["favorite"] = true }
        if let kind = [3: "portal", 4: "report", 5: "teach"][sidebarIndex] { params["kind"] = kind }
        if projectPopup.indexOfSelectedItem > 0 { params["project"] = projectPopup.titleOfSelectedItem }
        if tagPopup.indexOfSelectedItem > 0 { params["tag"] = tagPopup.titleOfSelectedItem }
        progress.startAnimation(nil)
        do {
            let results = try await engine.call("list", params)
            guard ticket == queryGeneration else { return }
            documents = (results["documents"] as? [JSON] ?? []).compactMap(KnowledgeDocument.init)
            if sidebarIndex == 2 {
                let recent = LumenPaths.defaults.stringArray(forKey: "reading.recent") ?? []
                documents.sort { (recent.firstIndex(of: $0.id) ?? Int.max) < (recent.firstIndex(of: $1.id) ?? Int.max) }
            }
            if query.isEmpty { matches = [] } else {
                let found = try await engine.call("search", ["query": query, "limit": 100])
                guard ticket == queryGeneration else { return }
                let allowed = Set(documents.map(\.id))
                matches = (found["matches"] as? [JSON] ?? []).compactMap(SearchMatch.init).filter { allowed.contains($0.documentID) }
                if matches.isEmpty { documents = [] }
            }
            documentsTable.reloadData(); countLabel.stringValue = query.isEmpty ? "\(documents.count) 份知识" : "\(matches.count) 处匹配"
            status.stringValue = query.isEmpty ? "本机知识库 · 页面可离线打开" : (matches.isEmpty ? "没有找到匹配内容" : "点击结果定位到对应章节和段落")
            if allDocuments.isEmpty || query.isEmpty {
                let all = try await engine.call("list", [:]); guard ticket == queryGeneration else { return }
                allDocuments = (all["documents"] as? [JSON] ?? []).compactMap(KnowledgeDocument.init)
                refillFilters()
            }
            let settings = try await engine.call("settings")
            let labels = (settings["settings"] as? JSON)?["labels"] as? [String: String] ?? [:]
            Remote.ui = FeedUI(copy: Dictionary(uniqueKeysWithValues: labels.map { ("mac.label." + $0.key, $0.value) }))
            generateButton.title = T("mac.label.generate", "生成知识"); importButton.title = T("mac.label.import", "导入 HTML"); saveButton.title = T("mac.label.save", "保存")
            searchField.placeholderString = T("mac.label.search", "全文搜索知识")
        } catch { if ticket == queryGeneration { showError(error) } }
        if ticket == queryGeneration { progress.stopAnimation(nil); libraryRefreshed = true; announceReadyIfNeeded() }
    }
    private func refillFilters() {
        let project = projectPopup.titleOfSelectedItem, tag = tagPopup.titleOfSelectedItem
        projectPopup.removeAllItems(); projectPopup.addItem(withTitle: "所有项目")
        projectPopup.addItems(withTitles: Array(Set(allDocuments.map(\.project).filter { !$0.isEmpty })).sorted())
        tagPopup.removeAllItems(); tagPopup.addItem(withTitle: "所有主题")
        tagPopup.addItems(withTitles: Array(Set(allDocuments.flatMap(\.tags))).sorted())
        if let project, projectPopup.itemTitles.contains(project) { projectPopup.selectItem(withTitle: project) }
        if let tag, tagPopup.itemTitles.contains(tag) { tagPopup.selectItem(withTitle: tag) }
    }

    func open(documentID: String, pageID: String? = nil, anchor: String? = nil) async {
        do { try captureMetadataDraft(throwOnFailure: true) }
        catch { showError(EngineFailure("draft_not_saved", "未能保存本机草稿，请先保存笔记再切换成果：" + error.localizedDescription)); return }
        openingGeneration += 1; let ticket = openingGeneration
        try? await persistPosition()
        guard ticket == openingGeneration else { return }
        progress.startAnimation(nil)
        do {
            let result = try await engine.call("get", ["document_id": documentID])
            guard ticket == openingGeneration, let value = result["document"] as? JSON, let document = KnowledgeDocument(value) else { return }
            selectedDocument = document; selectedPage = nil; pages = (result["pages"] as? [JSON] ?? []).compactMap(KnowledgePage.init)
            homePageID = (result["content"] as? JSON)?["home"] as? String ?? pages.first?.id
            var position: JSON = [:]
            let settings = (try? await engine.call("settings"))?["settings"] as? JSON ?? [:]
            if pageID == nil && settings["restore_reading"] as? Bool != false { position = (try? await engine.call("position", ["document_id": documentID]))?["position"] as? JSON ?? [:] }
            guard ticket == openingGeneration else { return }
            let wanted = pageID ?? position["page_id"] as? String
            let page = pages.first { $0.id == wanted } ?? pages.first { $0.absolutePath == document.entryPath } ?? pages.first
            pagePopup.removeAllItems(); pagePopup.addItems(withTitles: pages.map(\.title)); updateDocumentUI()
            LumenPaths.defaults.set(document.id, forKey: "reading.document")
            var recent = LumenPaths.defaults.stringArray(forKey: "reading.recent") ?? []; recent.removeAll { $0 == document.id }; recent.insert(document.id, at: 0)
            LumenPaths.defaults.set(Array(recent.prefix(100)), forKey: "reading.recent")
            if let page { await loadPage(page, scroll: pageID == nil ? position["scroll_y"] as? Double : 0, anchor: anchor ?? position["anchor"] as? String) }
            else { throw EngineFailure("page_missing", "这份知识没有可阅读页面。") }
            if !inspector.isHidden { await loadVersions() }
        } catch { if ticket == openingGeneration { showError(error) } }
        if ticket == openingGeneration { progress.stopAnimation(nil) }
    }
    func restoreLastReading() async {
        if let id = LumenPaths.defaults.string(forKey: "reading.document"), allDocuments.contains(where: { $0.id == id }) { await open(documentID: id) }
    }
    func loadPage(_ page: KnowledgePage, scroll: Double? = nil, anchor: String? = nil) async {
        let documentID = selectedDocument?.id
        try? await persistPosition()
        guard selectedDocument?.id == documentID && pages.contains(page) else { return }
        selectedPage = page; pendingScroll = scroll; pendingAnchor = anchor
        if let index = pages.firstIndex(where: { $0.id == page.id }) { pagePopup.selectItem(at: index) }
        guard let root = currentSiteRoot, isInsideSite(URL(fileURLWithPath: page.absolutePath), root: root) else {
            showError(EngineFailure("path_rejected", "此页面位于当前成果之外，未自动打开。")); return
        }
        reader.loadFileURL(URL(fileURLWithPath: page.absolutePath), allowingReadAccessTo: root)
        status.stringValue = page.title + " · v\(selectedDocument?.version ?? 1)"
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let offset = pendingScroll ?? 0, anchor = pendingAnchor ?? ""
        pendingScroll = nil; pendingAnchor = nil
        let encoded = (try? LumenPaths.string(["anchor": anchor])) ?? "{}"
        reader.evaluateJavaScript("(function(){const p=\(encoded); const e=p.anchor&&(document.getElementById(p.anchor)||Array.from(document.querySelectorAll('[data-feedback-block]')).find(e=>e.dataset.feedbackBlock===p.anchor)); if(e)e.scrollIntoView();else window.scrollTo(0,\(offset)); let t;window.addEventListener('scroll',()=>{clearTimeout(t);t=setTimeout(()=>window.webkit.messageHandlers.lumenPosition.postMessage({scroll_y:window.scrollY}),500)},{passive:true});})()")
        Task {
            await applyReadingSettings()
            let pageLoaded = selectedPage.map { reader.url?.path == $0.absolutePath || reader.url?.path == selectedDocument?.entryPath } ?? false
            announceReadyIfNeeded()
            if pageLoaded, let path = ProcessInfo.processInfo.environment["LUMEN_MEASURE_READY_FILE"], let document = selectedDocument, let page = selectedPage {
                let value: JSON = ["ready": true, "document_id": document.id, "page_id": page.id, "version": document.version, "pid": ProcessInfo.processInfo.processIdentifier]
                if let data = try? LumenPaths.data(value) { try? data.write(to: URL(fileURLWithPath: path), options: .atomic) }
            }
        }
        if let path = reader.url?.path, let page = pages.first(where: { $0.absolutePath == path }) { selectedPage = page; if let index = pages.firstIndex(of: page) { pagePopup.selectItem(at: index) } }
        else if reader.url?.path == selectedDocument?.entryPath, let page = pages.first(where: { $0.id == homePageID }) { selectedPage = page; if let index = pages.firstIndex(of: page) { pagePopup.selectItem(at: index) } }
        progress.stopAnimation(nil)
    }
    private func announceReadyIfNeeded() {
        guard !readyAnnounced, !reader.isLoading else { return }
        let pageLoaded = selectedPage.map { reader.url?.path == $0.absolutePath || reader.url?.path == selectedDocument?.entryPath } ?? false
        let emptyReady = libraryRefreshed && allDocuments.isEmpty && selectedDocument == nil
        guard pageLoaded || emptyReady else { return }
        readyAnnounced = true; window?.contentView?.layoutSubtreeIfNeeded(); LaneSignal.ready(pageLoaded ? "library-reader" : "library-empty")
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { showError(error) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { showError(error) }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" { decisionHandler(.allow); return }
        if url.isFileURL {
            if let document = selectedDocument {
                let root = currentSiteRoot ?? URL(fileURLWithPath: document.entryPath).deletingLastPathComponent()
                if !isInsideSite(url, root: root) {
                    if (navigationAction.navigationType == .linkActivated || navigationAction.navigationType == .backForward), let page = currentPageForLegacyURL(url) {
                        decisionHandler(.cancel); Task { await loadPage(page, scroll: 0, anchor: url.fragment) }; return
                    }
                    if navigationAction.navigationType == .linkActivated, NSApp.activationPolicy() != .prohibited {
                        NSWorkspace.shared.open(url); decisionHandler(.cancel); return
                    }
                    showError(EngineFailure("path_rejected", "此链接位于当前成果之外，未自动打开。")); decisionHandler(.cancel); return
                }
            }
            if navigationAction.navigationType == .linkActivated, let page = pages.first(where: { $0.absolutePath == url.path }) ?? (url.path == selectedDocument?.entryPath ? pages.first(where: { $0.id == homePageID }) : nil) {
                decisionHandler(.cancel); Task { await loadPage(page, scroll: 0, anchor: url.fragment) }; return
            }
            decisionHandler(.allow); return
        }
        // A click may intentionally follow a cited source; content cannot automatically launch external applications.
        if navigationAction.navigationType == .linkActivated && ["https", "http"].contains(url.scheme ?? ""), NSApp.activationPolicy() != .prohibited { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    /// A legacy <base> or history entry can point at an older version. Only registered current pages can be rebased.
    func currentPageForLegacyURL(_ url: URL) -> KnowledgePage? {
        let target = url.standardizedFileURL.path
        let matching = pages.filter { target.hasSuffix("/" + $0.relativePath) }
        return matching.count == 1 ? matching.first : nil
    }
    private var currentSiteRoot: URL? {
        guard let document = selectedDocument else { return nil }
        // A registered page's relative path is rooted in this version's complete site.
        // The entry itself can be a nested chapter, so its parent is not always the site root.
        if let page = pages.first(where: { $0.absolutePath == document.entryPath }) ?? pages.first {
            var root = URL(fileURLWithPath: page.absolutePath)
            for _ in page.relativePath.split(separator: "/") { root.deleteLastPathComponent() }
            // Keep the same URL spelling as loadFileURL. WebKit grants access to that
            // exact root; resolving /private/tmp to /tmp only on the root rejects the page.
            return root
        }
        return URL(fileURLWithPath: document.entryPath).deletingLastPathComponent()
    }
    private func isInsideSite(_ url: URL, root: URL) -> Bool {
        let target = url.resolvingSymlinksInPath().standardizedFileURL.path, base = root.resolvingSymlinksInPath().standardizedFileURL.path
        return target == base || target.hasPrefix(base + "/")
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let params = message.body as? JSON,
              let document = selectedDocument, let page = selectedPage else { return }
        guard let source = message.frameInfo.request.url, source.isFileURL,
              let root = currentSiteRoot, isInsideSite(source, root: root) else { return }
        let sourcePath = source.resolvingSymlinksInPath().standardizedFileURL.path
        let pagePath = URL(fileURLWithPath: page.absolutePath).resolvingSymlinksInPath().standardizedFileURL.path
        let entryPath = URL(fileURLWithPath: document.entryPath).resolvingSymlinksInPath().standardizedFileURL.path
        guard sourcePath == pagePath || (sourcePath == entryPath && page.id == homePageID) else { return }
        if message.name == "lumenPosition", let scroll = params["scroll_y"] as? Double {
            Task { _ = try? await engine.call("position", ["document_id": document.id, "page_id": page.id, "scroll_y": max(0, scroll), "anchor": ""]) }; return
        }
        guard message.name == "lumenSave",
              params["document_id"] as? String == document.id,
              params["page_id"] as? String == page.id,
              params["expected_version"] as? Int == document.version else {
            showError(EngineFailure("stale_page", "页面版本已变化，请重新打开后保存；当前编辑仍在页面中。")); return
        }
        Task { await saveFromReader(params, documentID: document.id, pageID: page.id) }
    }
    func saveFromReader(_ params: JSON, documentID: String, pageID: String) async {
        progress.startAnimation(nil)
        do {
            let result = try await engine.call("save", params)
            guard selectedDocument?.id == documentID && selectedPage?.id == pageID else { return }
            _ = try? await reader.callAsyncJavaScript("return window.LumenReader && window.LumenReader.saved(result)", arguments: ["result": result], in: nil, contentWorld: .page)
            await open(documentID: documentID, pageID: pageID)
            await refresh(); status.stringValue = "页面、批注与内容源已保存 · 可关闭后重开"
        } catch {
            if selectedDocument?.id == documentID {
                showError(error)
                let result: JSON = ["ok": false, "error": ["message": error.localizedDescription]]
                _ = try? await reader.callAsyncJavaScript("return window.LumenReader && window.LumenReader.saved(result)", arguments: ["result": result], in: nil, contentWorld: .page)
            }
        }
        progress.stopAnimation(nil)
    }
    func savePosition() {
        guard let document = selectedDocument, let page = selectedPage else { return }
        reader.evaluateJavaScript("window.scrollY") { [weak self] value, _ in
            guard let self, let scroll = value as? Double else { return }
            Task { _ = try? await self.engine.call("position", ["document_id": document.id, "page_id": page.id, "scroll_y": max(0, scroll), "anchor": ""], timeout: 5) }
        }
    }
    func persistPosition() async throws {
        try captureMetadataDraft(throwOnFailure: true)
        guard let document = selectedDocument, let page = selectedPage else { return }
        let scroll = try await reader.evaluateJavaScript("window.scrollY") as? Double ?? 0
        _ = try await engine.call("position", ["document_id": document.id, "page_id": page.id, "scroll_y": max(0, scroll), "anchor": ""], timeout: 5)
    }
    func applyReadingSettings() async {
        guard let result = try? await engine.call("settings"), let settings = result["settings"] as? JSON else { return }
        let font = settings["font_size"] as? Int ?? 17, theme = settings["theme"] as? String ?? "light"
        let dark = theme == "dark" || (theme == "system" && NSApp.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua)
        let css = "main p,main li,main td,article p,article li,article td{font-size:\(font)px!important}" + (dark ? ":root{--bg:#17212d;--paper:#202e3e;--ink:#e4edf6;--mute:#b1c4d7;--line:#3b4d61;--blue:#8fbeed;--tint:#263e55}.topbar,.search-box,.search-results{background:#202e3e!important;color:#e4edf6!important}body,main{background:#17212d!important;color:#e4edf6!important}h1,h2,h3,p,li,td{color:inherit!important}" : "")
        let encoded = (try? LumenPaths.string(["css": css, "theme": theme])) ?? "{}"
        let script = "(()=>{let p=\(encoded);let s=document.getElementById('lumen-reading-settings');if(!s){s=document.createElement('style');s.id='lumen-reading-settings';document.head.append(s)}s.textContent=p.css;document.documentElement.dataset.lumenTheme=p.theme;})()"
        _ = try? await reader.evaluateJavaScript(script)
        if theme == "dark" { reader.appearance = NSAppearance(named: .darkAqua) }
        else if theme == "light" { reader.appearance = NSAppearance(named: .aqua) }
        else { reader.appearance = nil }
    }
    private func parsedTags(_ value: String) -> [String] {
        value.replacingOccurrences(of: "，", with: ",").split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }
    private func draftURL(_ id: String) -> URL {
        let name = Data(id.utf8).base64EncodedString().replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "+", with: "-")
        return engine.stateDirectory.appendingPathComponent("metadata-drafts", isDirectory: true).appendingPathComponent(name + ".json")
    }
    private func writeDraft(_ draft: MetadataDraft?, id: String) throws {
        let url = draftURL(id)
        if let draft {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try JSONEncoder().encode(draft).write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } else if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
    }
    func captureMetadataDraft() {
        do { try captureMetadataDraft(throwOnFailure: true) } catch { showError(EngineFailure("draft_not_saved", "未能保存本机草稿，请先保存笔记再离开：" + error.localizedDescription)) }
    }
    private func captureMetadataDraft(throwOnFailure: Bool) throws {
        guard !bindingMetadata, let document = metadataBaseline else { return }
        var draft = metadataDrafts[document.id] ?? MetadataDraft(revision: document.metadataRevision, baseTitle: document.title, baseProject: document.project, baseTags: document.tags, baseNotes: document.notes)
        draft.title = titleField.stringValue == document.title ? nil : titleField.stringValue
        draft.project = projectField.stringValue == document.project ? nil : projectField.stringValue
        let tags = parsedTags(tagsField.stringValue); draft.tags = tags == document.tags ? nil : tags
        draft.notes = notes.string == document.notes ? nil : notes.string
        let value = draft.isEmpty ? nil : draft
        if metadataDrafts[document.id] != value || pendingDraftWrites.contains(document.id) {
            metadataDrafts[document.id] = value; pendingDraftWrites.insert(document.id)
            try writeDraft(value, id: document.id); pendingDraftWrites.remove(document.id)
        }
        notesDirty = value != nil
        if selectedDocument?.id == document.id { updateMetadataState() }
    }
    func controlTextDidChange(_ notification: Notification) {
        if let field = notification.object as? NSTextField, [titleField, projectField, tagsField].contains(where: { $0 === field }) { captureMetadataDraft() }
    }
    func textDidChange(_ notification: Notification) { if notification.object as? NSTextView === notes { captureMetadataDraft() } }
    private func updateMetadataState() {
        versionLabel.stringValue = selectedDocument.map { "当前版本 v\($0.version) · \($0.pageCount) 页" + (notesDirty ? " · 有草稿" : "") } ?? "尚未选择知识"
    }
    private func updateDocumentUI() {
        captureMetadataDraft()
        bindingMetadata = true; defer { bindingMetadata = false }
        let document = selectedDocument
        favoriteButton.image = NSImage(systemSymbolName: document?.favorite == true ? "star.fill" : "star", accessibilityDescription: "收藏")
        favoriteButton.toolTip = document?.favorite == true ? "取消收藏" : "收藏知识"
        titleField.stringValue = document?.title ?? ""; projectField.stringValue = document?.project ?? ""; tagsField.stringValue = document?.tags.joined(separator: ", ") ?? ""
        notes.string = document?.notes ?? ""
        metadataBaseline = document
        if let document {
            if metadataDrafts[document.id] == nil, let data = try? Data(contentsOf: draftURL(document.id)), let draft = try? JSONDecoder().decode(MetadataDraft.self, from: data) { metadataDrafts[document.id] = draft }
            if let draft = metadataDrafts[document.id] {
                if let title = draft.title { titleField.stringValue = title }; if let project = draft.project { projectField.stringValue = project }
                if let tags = draft.tags { tagsField.stringValue = tags.joined(separator: ", ") }; if let text = draft.notes { notes.string = text }
            }
        }
        notesDirty = document.flatMap { metadataDrafts[$0.id] } != nil; updateMetadataState()
        sourceLabel.stringValue = document?.sourcePaths.joined(separator: "\n") ?? "尚未选择来源"
        for control in [titleField, projectField, tagsField, versionPopup, favoriteButton] as [NSControl] { control.isEnabled = document != nil }
    }
    @objc func revealSource() {
        guard let path = selectedDocument?.primarySource else { showError(EngineFailure("source_missing", "这份知识没有登记原始来源。")); return }
        guard FileManager.default.fileExists(atPath: path) else { showError(EngineFailure("source_missing", "原始来源已不在当前路径，可复制来源信息或重新选择资料。")); return }
        if NSApp.activationPolicy() != .prohibited { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
    }
    @objc func toggleFavorite() {
        guard let document = selectedDocument else { return }
        Task { await applyMetadata(["favorite": !document.favorite], id: document.id) }
    }
    @objc func saveMetadata() {
        guard let document = selectedDocument else { return }
        captureMetadataDraft()
        guard let draft = metadataDrafts[document.id] else { status.stringValue = "笔记与信息没有未保存的修改"; return }
        Task { await applyMetadata(draftParams(draft), id: document.id) }
    }
    private func draftParams(_ draft: MetadataDraft) -> JSON {
        var params: JSON = ["expected_metadata_revision": draft.revision]
        if let title = draft.title { params["title"] = title }; if let project = draft.project { params["project"] = project }
        if let tags = draft.tags { params["tags"] = tags }; if let notes = draft.notes { params["notes"] = notes }
        return params
    }
    func applyMetadata(_ params: JSON, id: String) async {
        guard let before = selectedDocument, before.id == id else { return }
        captureMetadataDraft()
        var request = params; request["document_id"] = id
        if request["expected_version"] == nil { request["expected_version"] = before.version }
        if request["expected_metadata_revision"] == nil { request["expected_metadata_revision"] = before.metadataRevision }
        do {
            let result = try await engine.call("metadata", request)
            if let value = result["document"] as? JSON, let updated = KnowledgeDocument(value) {
                if selectedDocument?.id == id { captureMetadataDraft() }
                if var draft = metadataDrafts[id] {
                    if let title = request["title"] as? String, draft.title == title { draft.title = nil }
                    if let project = request["project"] as? String, draft.project == project { draft.project = nil }
                    if let tags = request["tags"] as? [String], draft.tags == tags { draft.tags = nil }
                    if let notes = request["notes"] as? String, draft.notes == notes { draft.notes = nil }
                    if draft.revision == (request["expected_metadata_revision"] as? Int) && draft.matchesBase(before) { draft.rebase(updated) }
                    metadataDrafts[id] = draft.isEmpty ? nil : draft; try writeDraft(metadataDrafts[id], id: id)
                }
                // A save may finish after the reader has moved to another result.
                // Acknowledge that result's durable draft regardless of current selection.
                if selectedDocument?.id == id {
                    metadataBaseline = nil
                    selectedDocument = updated; updateDocumentUI()
                    if updated.version != before.version { await open(documentID: id, pageID: selectedPage?.id) }
                }
            }
            await refresh(); status.stringValue = notesDirty ? "笔记与信息有未保存的修改，草稿已保留" : "笔记与信息已保存"
        } catch {
            captureMetadataDraft(); showError(error)
            if (error as? EngineFailure)?.code == "conflict", metadataDrafts[id] != nil { status.stringValue = "笔记与信息已在其他窗口修改。你的草稿仍保留，可查看已保存信息后再决定。" }
        }
    }
    @objc func toggleArchive() {
        guard let document = selectedDocument else { return }
        Task { await applyMetadata(["archived": !document.archived], id: document.id) }
    }
    @objc func reviewSavedMetadata() {
        guard let document = selectedDocument else { return }
        captureMetadataDraft()
        Task { [self] in
            do {
                let result = try await engine.call("get", ["document_id": document.id])
                guard selectedDocument?.id == document.id, let value = result["document"] as? JSON, let current = KnowledgeDocument(value) else { return }
                guard NSApp.activationPolicy() != .prohibited, let window else { return }
                let alert = NSAlert(); alert.messageText = "已保存的笔记与信息"
                alert.informativeText = "标题：\(current.title)\n项目：\(current.project)\n标签：\(current.tags.joined(separator: ", "))\n\n个人笔记：\n\(current.notes)"
                alert.addButton(withTitle: "继续编辑草稿"); alert.addButton(withTitle: "使用已保存信息")
                if metadataDrafts[document.id] != nil { alert.addButton(withTitle: "保存我的修改") }
                alert.beginSheetModal(for: window) { [weak self] response in
                    guard let self, self.selectedDocument?.id == document.id else { return }
                    if response == .alertSecondButtonReturn {
                        do {
                            try self.writeDraft(nil, id: document.id); self.metadataDrafts[document.id] = nil; self.metadataBaseline = nil
                            Task { await self.open(documentID: document.id, pageID: self.selectedPage?.id) }
                        }
                        catch { self.showError(error) }
                    } else if response == .alertThirdButtonReturn, let draft = self.metadataDrafts[document.id] {
                        var params = self.draftParams(draft); params["expected_metadata_revision"] = current.metadataRevision; params["expected_version"] = current.version
                        Task { await self.applyMetadata(params, id: document.id) }
                    }
                }
            } catch { showError(error) }
        }
    }
    func loadVersions() async {
        guard let document = selectedDocument else { return }
        do {
            let result = try await engine.call("versions", ["document_id": document.id])
            guard selectedDocument?.id == document.id else { return }
            versions = result["versions"] as? [JSON] ?? []; versionPopup.removeAllItems()
            for version in versions { versionPopup.addItem(withTitle: "v\(version["version"] as? Int ?? 0) · \(version["reason"] as? String ?? "保存")") }
        } catch { showError(error) }
    }
    @objc func restoreVersion() {
        guard let document = selectedDocument, versions.indices.contains(versionPopup.indexOfSelectedItem), let version = versions[versionPopup.indexOfSelectedItem]["version"] as? Int else { return }
        Task { await restore(version: version, document: document) }
    }
    func restore(version: Int, document: KnowledgeDocument) async {
        do {
            _ = try await engine.call("restore", ["document_id": document.id, "version": version, "expected_version": document.version])
            await open(documentID: document.id); await refresh(); status.stringValue = "已从 v\(version) 恢复为新版本，历史仍然保留"
        } catch { showError(error) }
    }
    @objc func compareVersion() {
        guard let document = selectedDocument, versions.indices.contains(versionPopup.indexOfSelectedItem), let version = versions[versionPopup.indexOfSelectedItem]["version"] as? Int else { return }
        Task {
            do {
                let result = try await engine.call("compare", ["document_id": document.id, "from_version": version, "to_version": document.version])
                let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 760, height: 520), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: true)
                panel.title = "\(document.title) · v\(version) → v\(document.version)"
                let text = NSTextView(); text.isEditable = false; text.font = .systemFont(ofSize: 14); text.textContainerInset = NSSize(width: 14, height: 14)
                let changes = result["changes"] as? [JSON] ?? []
                text.string = "v\(version) → v\(document.version) · \(changes.count) 处变化\n\n" + changes.enumerated().map { index, change in
                    "\(index + 1). \(changeLocation(change["path"] as? String ?? ""))\n\n原来：\n\(changeText(change["before"]))\n\n现在：\n\(changeText(change["after"]))\n\n────────────\n"
                }.joined(separator: "\n")
                if changes.isEmpty { text.string += "两版内容一致。" }
                fill(scroll(text), in: panel.contentView!, inset: 12)
                if NSApp.activationPolicy() != .prohibited { panel.center(); panel.makeKeyAndOrderFront(nil) }
            } catch { showError(error) }
        }
    }
    private func changeLocation(_ path: String) -> String {
        let parts = path.split(separator: "/").map(String.init)
        var labels: [String] = []
        for (index, part) in parts.enumerated() {
            if let number = Int(part), index > 0 {
                let unit = ["pages": "页", "blocks": "段", "items": "条", "rows": "行", "headers": "列"][parts[index - 1]] ?? "项"
                labels.append("第\(number + 1)\(unit)")
            } else if let label = ["title": "标题", "summary": "摘要", "text": "正文", "subtitle": "说明", "notes": "笔记", "feedback": "批注", "favorite": "收藏", "tags": "主题", "project": "项目"][part] { labels.append(label) }
        }
        return labels.isEmpty ? "内容" : labels.joined(separator: " · ")
    }
    private func changeText(_ value: Any?) -> String {
        guard let value, !(value is NSNull) else { return "无" }
        if let text = value as? String { return text.isEmpty ? "空" : text }
        if let list = value as? [Any] { return list.map { changeText($0) }.joined(separator: "\n") }
        return String(describing: value)
    }
    func chooseImport() {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
        panel.message = "选择 HTML 文件、门户目录或 PPTX，关联资源会一起收录。"
        panel.beginSheetModal(for: window!) { [weak self] response in guard response == .OK, let url = panel.url else { return }; Task { await self?.importURL(url) } }
    }
    func importURL(_ url: URL) async {
        progress.startAnimation(nil); status.stringValue = "正在收录 HTML 与关联资源…"
        do {
            let result = try await engine.call(url.pathExtension.lowercased() == "pptx" ? "convert" : "import", url.pathExtension.lowercased() == "pptx" ? ["source": url.path, "kind": "ppt"] : ["path": url.path, "include_test": false])
            await refresh(); if let value = result["document"] as? JSON, let id = value["id"] as? String { await open(documentID: id) }
            let validation = result["validation"] as? JSON ?? [:], warnings = validation["warnings"] as? [JSON] ?? []
            if validation["ok"] as? Bool == false || !warnings.isEmpty {
                status.textColor = .systemOrange; status.stringValue = "已收录，部分资源缺失；页面可以打开，可查看原始来源补齐资源"
                status.toolTip = warnings.compactMap { $0["path"] as? String }.joined(separator: "\n")
            } else {
                status.textColor = .secondaryLabelColor; status.stringValue = result["notice"] as? String ?? "已收录，可直接阅读和编辑"
                status.toolTip = status.stringValue
            }
        } catch { showError(error) }
        progress.stopAnimation(nil)
    }
    func chooseGraphSource() {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.message = "选择需要理解的目录，生成可逐层阅读的目录图谱。"
        panel.beginSheetModal(for: window!) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            Task {
                self.progress.startAnimation(nil)
                do {
                    let result = try await self.engine.call("convert", ["source": url.path, "kind": "graph"])
                    await self.refresh(); if let value = result["document"] as? JSON, let id = value["id"] as? String { await self.open(documentID: id) }
                    self.status.stringValue = "目录图谱已生成并收录"
                } catch { self.showError(error) }
                self.progress.stopAnimation(nil)
            }
        }
    }
    func exportSelected() {
        guard let document = selectedDocument else { return }
        let panel = NSSavePanel(); panel.canCreateDirectories = true; panel.nameFieldStringValue = document.title + ".zip"
        panel.beginSheetModal(for: window!) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            Task { do { _ = try await self.engine.call("export", ["document_id": document.id, "output": url.path]); self.status.stringValue = "成果已导出，包含完整关联资源" } catch { self.showError(error) } }
        }
    }
    func showGenerateSheet() {
        Task {
            let settings = (try? await engine.call("settings"))?["settings"] as? JSON ?? [:]
            let sheet = generateSheet ?? GenerateSheetController(owner: self); generateSheet = sheet
            sheet.prepare(selectedDocument)
            sheet.kind.selectItem(at: ["portal", "report", "teach"].firstIndex(of: settings["default_kind"] as? String ?? "portal") ?? 0)
            window?.beginSheet(sheet.window!, completionHandler: nil)
        }
    }
    func generate(_ params: JSON) async {
        do {
            let result = try await engine.call("generate", params)
            let task = result["task"] as? JSON ?? result
            guard let id = task["id"] as? String ?? task["task_id"] as? String else { throw EngineFailure("task_missing", "生成引擎没有返回任务编号。") }
            observeTask(id); status.stringValue = "知识生成已开始 · 可关闭窗口，任务会继续"
        } catch { showError(error) }
    }
    func resumeTaskObservation() async {
        if let result = try? await engine.call("tasks"), let tasks = result["tasks"] as? [JSON], let task = tasks.first(where: { ["queued", "running", "waiting", "cancelling"].contains($0["status"] as? String ?? "") }), let id = task["id"] as? String ?? task["task_id"] as? String { observeTask(id) }
    }
    private func observeTask(_ id: String) {
        observingTaskID = id; taskTimer?.invalidate()
        taskTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in Task { await self?.pollTask() } }
        Task { await pollTask() }
    }
    func stopObservation() { taskTimer?.invalidate(); taskTimer = nil; searchTimer?.invalidate() }
    private func pollTask() async {
        guard let id = observingTaskID else { return }
        do {
            let result = try await engine.call("task", ["task_id": id]); let task = result["task"] as? JSON ?? result
            guard id == observingTaskID else { return }
            let state = task["status"] as? String ?? "unknown"
            if ["completed", "succeeded", "done"].contains(state) {
                stopObservation(); observingTaskID = nil
                let output = task["result"] as? JSON ?? [:]
                let document = output["document"] as? JSON ?? task["document"] as? JSON ?? [:]
                await refresh(); if let documentID = document["id"] as? String ?? task["document_id"] as? String { await open(documentID: documentID) }
                status.stringValue = "知识已生成并入库"
            } else if ["failed", "cancelled", "canceled", "blocked", "interrupted"].contains(state) {
                stopObservation(); observingTaskID = nil
                let error = task["error"] as? JSON
                status.stringValue = error?["message"] as? String ?? "生成任务\(state == "failed" ? "失败" : "已停止")，可通过任务接口查询或恢复"
            } else { status.stringValue = "正在生成知识 · \(task["step"] as? String ?? task["checkpoint"] as? String ?? state)" }
        } catch { showError(error) }
    }
    func showError(_ error: Error) { progress.stopAnimation(nil); status.textColor = .systemOrange; status.stringValue = error.localizedDescription; status.toolTip = error.localizedDescription }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        do { try captureMetadataDraft(throwOnFailure: true); return true }
        catch { showError(EngineFailure("draft_not_saved", "未能保存本机草稿，请先保存笔记再关闭窗口：" + error.localizedDescription)); return false }
    }
    func windowWillClose(_ notification: Notification) { captureMetadataDraft(); savePosition() }
}

final class GenerateSheetController: NSWindowController {
    weak var library: LibraryWindowController?
    let source = NSTextField(), request = NSTextView(), project = NSTextField(), kind = NSPopUpButton()
    let mode = NSSegmentedControl(labels: ["新建知识", "更新当前成果"], trackingMode: .selectOne, target: nil, action: nil)
    let explanation = NSTextField(wrappingLabelWithString: "")
    private var context: KnowledgeDocument?
    init(owner: LibraryWindowController) {
        self.library = owner
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 680, height: 540), styleMask: [.titled, .closable], backing: .buffered, defer: true)
        panel.title = "生成知识"; panel.isReleasedWhenClosed = false
        super.init(window: panel)
        source.placeholderString = "项目目录、资料目录或文件"; project.placeholderString = "所属项目（可选）"
        mode.target = self; mode.action = #selector(modeChanged); mode.selectedSegment = 0
        explanation.font = .systemFont(ofSize: 12); explanation.textColor = .secondaryLabelColor
        kind.addItems(withTitles: ["门户 · 逐层理解", "阅读与报告", "教学 · 按顺序讲解"])
        let choose = NSButton(title: "选择来源…", target: self, action: #selector(chooseSource)); choose.bezelStyle = .rounded
        let location = NSStackView(views: [source, choose]); location.spacing = 8; source.setContentHuggingPriority(.defaultLow, for: .horizontal)
        request.font = .systemFont(ofSize: 14); request.isRichText = false; request.textContainerInset = NSSize(width: 10, height: 10)
        let scroll = NSScrollView(); scroll.documentView = request; scroll.hasVerticalScroller = true; scroll.borderType = .bezelBorder
        request.isVerticallyResizable = true; request.autoresizingMask = [.width]; request.textContainer?.widthTracksTextView = true
        let generate = NSButton(title: "开始生成", target: self, action: #selector(submit)); generate.bezelStyle = .rounded; generate.keyEquivalent = "\r"
        let cancel = NSButton(title: "取消", target: self, action: #selector(closeSheet)); cancel.bezelStyle = .rounded; cancel.keyEquivalent = "\u{1b}"
        let actions = NSStackView(views: [cancel, generate]); actions.spacing = 8
        let label = NSTextField(wrappingLabelWithString: "描述你希望理解什么。Lumen 会让接入的 agent 整理来源，再按统一标准生成可编辑 HTML，并自动加入知识库。")
        label.font = .systemFont(ofSize: 13); label.textColor = .secondaryLabelColor
        let stack = NSStackView(views: [label, mode, explanation, location, kind, project, NSTextField(labelWithString: "生成要求"), scroll, actions]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false; panel.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: panel.contentView!.leadingAnchor, constant: 24), stack.trailingAnchor.constraint(equalTo: panel.contentView!.trailingAnchor, constant: -24), stack.topAnchor.constraint(equalTo: panel.contentView!.topAnchor, constant: 24), stack.bottomAnchor.constraint(equalTo: panel.contentView!.bottomAnchor, constant: -24), scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 170)] + [label, explanation, location, project, scroll].map { $0.widthAnchor.constraint(equalTo: stack.widthAnchor) })
    }
    required init?(coder: NSCoder) { fatalError() }
    func prepare(_ document: KnowledgeDocument?) { context = document; mode.setEnabled(document != nil, forSegment: 1); mode.selectedSegment = 0; modeChanged() }
    @objc func modeChanged() {
        if mode.selectedSegment == 1, let context {
            project.stringValue = context.project; kind.selectItem(at: ["portal", "report", "teach"].firstIndex(of: context.kind) ?? 0)
            if let path = context.primarySource, FileManager.default.fileExists(atPath: path) { source.stringValue = path; explanation.stringValue = "更新「\(context.title)」v\(context.version)，保留人工修改、笔记和批注；冲突会明确提示。" }
            else { source.stringValue = ""; explanation.stringValue = "当前成果的原来源不可用，请选择项目或资料后更新；人工修改与历史仍会保留。" }
        } else { explanation.stringValue = "生成新的成果并自动加入知识库。" }
    }
    @objc func chooseSource() {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = true
        panel.beginSheetModal(for: window!) { [weak self] response in if response == .OK { self?.source.stringValue = panel.url?.path ?? "" } }
    }
    @objc func closeSheet() { library?.window?.endSheet(window!); window?.orderOut(nil) }
    @objc func submit() {
        guard !source.stringValue.isEmpty, !request.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { library?.showError(EngineFailure("missing_input", "请选择来源并填写希望理解的内容。")); return }
        var params: JSON = ["source": source.stringValue, "request": request.string, "kind": ["portal", "report", "teach"][kind.indexOfSelectedItem], "project": project.stringValue]
        if mode.selectedSegment == 1, let context { params["document_id"] = context.id; params["expected_version"] = context.version }
        closeSheet(); Task { await library?.generate(params) }
    }
}
