import AppKit
import WebKit

@main @MainActor enum LumenMain {
    static func main() {
        let words = Array(CommandLine.arguments.dropFirst())
        if AppLifecycleCLI.handles(words.first) { exit(AppLifecycleCLI.run(words, product: ProductLifecycle.product())) }
        if words.first == "--help" || words.first == "-h" {
            _ = EngineClient.relay(words)
            print("\n原生配置与更新：\n" + AppLifecycleCLI.helpRead("lumen") + "\n" + AppLifecycleCLI.helpWrite("lumen"))
            print("仅在窗口中：阅读、文件选择与配置窗口；其他业务均可通过 lumen CLI / API / MCP 使用。")
            return
        }
        if words.first == "--version" { print(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0"); return }
        let demoPath = words.first == "capture-demo" && words.count == 3 && words[1] == "--output" ? words[2] : nil
        if words.first == "capture-demo" && (demoPath == nil || demoPath?.isEmpty == true) { fputs("capture-demo --output <目录>\n", stderr); exit(64) }
        let testing = words.contains("--product-basics-self-test") || words.contains("--ui-self-test") || words.contains("--management-self-test") || words.contains("--snapshot") || demoPath != nil
        let probePath = words.first == "--lifecycle-follow-probe" && words.count == 2 ? words[1] : nil
        let background = words.contains("--background") || words.contains("-lane_quiet") || probePath != nil
        if let probePath {
            guard !probePath.isEmpty, ["LUMEN_STATE_DIR", "LUMEN_DEFAULTS_DOMAIN", "APP_LIFECYCLE_SUPPORT_DIR", "APP_LIFECYCLE_CLOUD_DIR"].allSatisfy({ !(ProcessInfo.processInfo.environment[$0] ?? "").isEmpty }) else { fputs("生命周期探针只允许在完整隔离环境中运行。\n", stderr); exit(64) }
        }
        if !words.isEmpty && !testing && !background { exit(EngineClient.relay(words)) }
        if testing {
            if ProcessInfo.processInfo.environment["LUMEN_STATE_DIR"] == nil {
                let isolated = FileManager.default.temporaryDirectory.appendingPathComponent("lumen-native-" + UUID().uuidString).path
                setenv("LUMEN_STATE_DIR", isolated, 1)
            }
            let state = ProcessInfo.processInfo.environment["LUMEN_STATE_DIR"]!
            setenv("APP_LIFECYCLE_SUPPORT_DIR", state + "/lifecycle", 1)
            if ProcessInfo.processInfo.environment["LUMEN_DEFAULTS_DOMAIN"] == nil { setenv("LUMEN_DEFAULTS_DOMAIN", "cyou.tianli.Lumen.test." + UUID().uuidString, 1) }
        }
        let app = NSApplication.shared
        if testing || background { app.setActivationPolicy(.prohibited) } else { app.setActivationPolicy(.regular) }
        let delegate = LumenAppDelegate(testing: testing, quiet: background, probePath: probePath, demoPath: demoPath)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}

@MainActor final class LumenAppDelegate: NSObject, NSApplicationDelegate {
    let testing: Bool, quiet: Bool
    let probePath: String?
    let demoPath: String?
    var controller: LibraryWindowController?
    private var terminating = false
    init(testing: Bool, quiet: Bool, probePath: String? = nil, demoPath: String? = nil) { self.testing = testing; self.quiet = quiet; self.probePath = probePath; self.demoPath = demoPath }
    func applicationDidFinishLaunching(_ notification: Notification) {
        // LaunchServices can finalize the bundle's UIElement policy after main.
        // Reassert the explicit test/background contract at the launch boundary.
        if testing || quiet { NSApp.setActivationPolicy(.prohibited) }
        buildMenus()
        let controller = LibraryWindowController(engine: EngineClient()); self.controller = controller
        LaneSignal.applyWindowFrame()
        ProductLifecycle.install(controller.engine, refresh: { [weak self] in self?.refreshLibrary() })
        ProductLifecycle.settings?.changed = { Task { await controller.refresh(); await controller.applyReadingSettings() } }
        if let demoPath {
            Task { await NativeDemo.run(controller, output: URL(fileURLWithPath: demoPath, isDirectory: true)) }
        } else if let probePath {
            NativeLifecycleProbe.start(path: probePath)
        } else if CommandLine.arguments.contains("--product-basics-self-test") {
            Task { await NativeProductBasicsTest.run(controller) }
        } else if testing {
            Task { await NativeSelfTest.run(controller) }
        } else {
            if !quiet { controller.showWindow(nil); controller.window?.makeKeyAndOrderFront(nil) }
            Task {
                await controller.refresh(); await controller.restoreLastReading()
                if controller.selectedDocument == nil, let first = controller.allDocuments.first { await controller.open(documentID: first.id) }
                await controller.resumeTaskObservation()
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        Task { for filename in filenames { await controller?.importURL(URL(fileURLWithPath: filename)) }; sender.reply(toOpenOrPrint: .success) }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminating else { return .terminateLater }
        terminating = true; controller?.stopObservation()
        Task {
            do { try await controller?.persistPosition(); sender.reply(toApplicationShouldTerminate: true) }
            catch { controller?.showError(EngineFailure("position_not_saved", "退出前未能保存阅读位置：" + error.localizedDescription)); terminating = false; sender.reply(toApplicationShouldTerminate: false) }
        }
        return .terminateLater
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard !quiet && !testing else { return false }
        controller?.showWindow(nil); controller?.window?.makeKeyAndOrderFront(nil); return true
    }
    private func item(_ title: String, _ selector: Selector?, _ key: String = "", target: AnyObject? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: key); item.target = target; return item
    }
    func buildMenus() {
        let main = NSMenu()
        let application = NSMenu(title: "Lumen"), applicationItem = NSMenuItem(); applicationItem.submenu = application; main.addItem(applicationItem)
        application.addItem(item("关于 Lumen · 知页", #selector(about), target: self))
        application.addItem(.separator()); application.addItem(item("隐藏 Lumen", #selector(NSApplication.hide(_:)), "h"))
        application.addItem(.separator()); application.addItem(item("退出 Lumen", #selector(NSApplication.terminate(_:)), "q"))
        let file = NSMenu(title: "文件"), fileItem = NSMenuItem(title: "文件", action: nil, keyEquivalent: ""); fileItem.submenu = file; main.addItem(fileItem)
        file.addItem(item("生成知识…", #selector(generate), "n", target: self)); file.addItem(item("导入 HTML / PPTX…", #selector(importHTML), "o", target: self))
        file.addItem(item("生成目录图谱…", #selector(graph), target: self))
        file.addItem(item("保存页面", #selector(save), "s", target: self)); file.addItem(.separator())
        file.addItem(item("导出成果…", #selector(exportDocument), "e", target: self))
        file.addItem(item(T("mac.label.rename","重命名…"), #selector(renameDocument), target:self))
        file.addItem(item(T("mac.label.duplicate","复制文档…"), #selector(duplicateDocument), target:self))
        file.addItem(item(T("mac.label.copy_ai","复制给 AI"), #selector(copyAI), target:self))
        file.addItem(item(T("mac.label.trash","移到废纸篓"), #selector(trashDocument), target:self))
        file.addItem(.separator()); file.addItem(item("关闭窗口", #selector(NSWindow.performClose(_:)), "w"))
        let edit = NSMenu(title: "编辑"), editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: ""); editItem.submenu = edit; main.addItem(editItem)
        edit.addItem(item("撤销", Selector(("undo:")), "z")); let redo = item("重做", Selector(("redo:")), "z"); redo.keyEquivalentModifierMask = [.command, .shift]; edit.addItem(redo)
        edit.addItem(.separator()); edit.addItem(item("剪切", #selector(NSText.cut(_:)), "x")); edit.addItem(item("复制", #selector(NSText.copy(_:)), "c")); edit.addItem(item("粘贴", #selector(NSText.paste(_:)), "v")); edit.addItem(item("全选", #selector(NSText.selectAll(_:)), "a"))
        let view = NSMenu(title: "显示"), viewItem = NSMenuItem(title: "显示", action: nil, keyEquivalent: ""); viewItem.submenu = view; main.addItem(viewItem)
        view.addItem(item("搜索知识", #selector(search), "f", target: self))
        view.addItem(item(T("mac.label.information","文档信息"), #selector(information), "i", target:self))
        view.addItem(item("笔记与版本", #selector(inspector), target: self)); view.addItem(item("返回上一页", #selector(back), "[", target: self))
        view.addItem(item("生成任务", #selector(tasks), target: self))
        let help = NSMenu(title: "帮助"), helpItem = NSMenuItem(title: "帮助", action: nil, keyEquivalent: ""); helpItem.submenu = help; main.addItem(helpItem)
        help.addItem(item("生成标准", #selector(guide), target: self)); help.addItem(item("发现已有 HTML…", #selector(discover), target: self))
        let window = NSMenu(title: "窗口"), windowItem = NSMenuItem(title: "窗口", action: nil, keyEquivalent: ""); windowItem.submenu = window; main.addItem(windowItem)
        window.addItem(item("显示知识库", #selector(showLibrary), "1", target: self)); window.addItem(item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m")); window.addItem(item("缩放", #selector(NSWindow.performZoom(_:))))
        NSApp.mainMenu = main; NSApp.windowsMenu = window
    }
    @objc func about() { NSApp.orderFrontStandardAboutPanel(options: [.applicationName: LumenPaths.displayName]) }
    @objc func generate() { controller?.showGenerateSheet() }
    @objc func importHTML() { controller?.chooseImport() }
    @objc func graph() { controller?.chooseGraphSource() }
    @objc func discover() { controller?.showDiscoverSheet() }
    @objc func guide() { controller?.showGuide() }
    @objc func save() { controller?.savePage() }
    @objc func exportDocument() { controller?.exportSelected() }
    @objc func renameDocument() { controller?.renameDocument(nil) }
    @objc func duplicateDocument() { controller?.duplicateDocument(nil) }
    @objc func copyAI() { controller?.copyCurrentForAI() }
    @objc func information() { controller?.showDocumentInformation() }
    @objc func trashDocument() { controller?.trashDocuments(nil) }
    func refreshDocumentMenuLabels() {
        let labels: [Selector:(String,String)] = [#selector(renameDocument):("rename","重命名…"),#selector(duplicateDocument):("duplicate","复制文档…"),#selector(copyAI):("copy_ai","复制给 AI"),#selector(trashDocument):("trash","移到废纸篓"),#selector(information):("information","文档信息")]
        for top in NSApp.mainMenu?.items ?? [] { for item in top.submenu?.items ?? [] { if let action = item.action, let (key,fallback) = labels[action] { item.title = T("mac.label."+key,fallback) } } }
    }
    @objc func search() { controller?.focusSearch() }
    @objc func inspector() { controller?.toggleInspector() }
    @objc func tasks() { controller?.showTasks() }
    @objc func back() { controller?.reader.goBack() }
    @objc func refreshLibrary() { Task { await controller?.refresh() } }
    @objc func showLibrary() { controller?.showWindow(nil); controller?.window?.makeKeyAndOrderFront(nil) }
}
