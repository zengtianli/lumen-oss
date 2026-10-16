import AppKit

/// Actual product views and actions, under the same prohibited policy as the native suite.
@MainActor enum NativeProductBasicsTest {
    static func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
    static func wait(_ condition: () async throws -> Bool) async throws {
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            if try await condition() { return }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        throw AppUpdateError("真实产品动作没有在期限内完成。")
    }
    static func run(_ controller: LibraryWindowController) async {
        var checks: [String: Bool] = [:]
        var failureMessage: String?
        let output = URL(fileURLWithPath: ProcessInfo.processInfo.environment["LUMEN_UI_OUT"] ?? "", isDirectory: true)
        do {
            // LaunchServices may finish the UIElement policy after didFinishLaunching;
            // restore the explicit quiet contract at this asynchronous entry boundary.
            NSApp.setActivationPolicy(.prohibited)
            guard NSApp.activationPolicy() == .prohibited,
                  ["LUMEN_STATE_DIR", "LUMEN_DEFAULTS_DOMAIN", "APP_LIFECYCLE_CLOUD_DIR", "LUMEN_UI_OUT"].allSatisfy({ !(ProcessInfo.processInfo.environment[$0] ?? "").isEmpty }),
                  let settings = ProductLifecycle.settings, let shortcuts = ProductLifecycle.shortcuts else {
                throw AppUpdateError("基础功能自检须有完整隔离环境，禁止激活；policy=\(NSApp.activationPolicy().rawValue)，settings=\(ProductLifecycle.settings != nil)，shortcuts=\(ProductLifecycle.shortcuts != nil)。")
            }
            try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
            await settings.load()
            let snapshot = try AppLifecycleUI.shared.offscreenSnapshot(to: output.appendingPathComponent("settings-light.png"))
            checks["settings.real_view_rendered"] = snapshot.values.allSatisfy { $0 }
            checks["settings.real_control_target"] = settings.font.target === settings && settings.font.action == #selector(ProductSettings.save(_:))
            settings.font.selectItem(at: 7)
            checks["settings.control_action_dispatched"] = NSApp.sendAction(settings.font.action!, to: settings.font.target, from: settings.font)
            try await wait {
                let value = try await controller.engine.call("settings")
                return (value["settings"] as? JSON)?["font_size"] as? Int == 19
            }
            await settings.load()
            checks["settings.control_saved_to_engine"] = settings.font.indexOfSelectedItem == 7
            let content: JSON = ["title": "基础能力隔离样例", "pages": [["id": "overview", "title": "刷新动作", "blocks": [["id": "text", "type": "prose", "text": "通过真实刷新菜单读取此成果。"]]]]]
            _ = try await controller.engine.call("build", ["content": content, "kind": "portal"])
            shortcuts.performAction()
            try await wait { !controller.allDocuments.isEmpty }
            checks["shortcuts.real_refresh_action"] = controller.allDocuments.contains { $0.title == "基础能力隔离样例" } && shortcuts.menuItem?.action == #selector(AppShortcuts.performAction)
            settings.shortcutBinding.selectItem(at: 2)
            settings.save(settings.shortcutBinding)
            let saved = shortcuts.binding.dictionary
            settings.shortcutEnabled.state = .off; settings.save(settings.shortcutEnabled)
            checks["shortcuts.disabled_key_keeps_binding"] = !shortcuts.enabled && shortcuts.menuItem?.keyEquivalent == "" && NSDictionary(dictionary: saved).isEqual(to: shortcuts.binding.dictionary)
            settings.shortcutEnabled.state = .on; settings.save(settings.shortcutEnabled)
            checks["shortcuts.restore_same_binding"] = shortcuts.enabled && shortcuts.menuItem?.keyEquivalent == "r" && shortcuts.binding.modifiers == [.command, .shift]
            do { try shortcuts.setBinding(AppShortcuts.Binding(key: "q", modifiers: .command)); checks["shortcuts.reserved_rejected"] = false }
            catch { checks["shortcuts.reserved_rejected"] = NSDictionary(dictionary: saved).isEqual(to: shortcuts.binding.dictionary) }
            do { try shortcuts.setBinding(AppShortcuts.Binding(key: "f", modifiers: .command)); checks["shortcuts.menu_conflict_rejected"] = false }
            catch { checks["shortcuts.menu_conflict_rejected"] = NSDictionary(dictionary: saved).isEqual(to: shortcuts.binding.dictionary) }
            AppLifecycleUI.shared.checkForUpdates(present: false)
            try await wait {
                NSApp.windows.compactMap(\.contentView).flatMap(descendants).compactMap { ($0 as? NSTextField)?.stringValue }.contains { $0.contains("当前已是此渠道最新版") }
            }
            checks["updates.real_ui_check_result"] = NSApp.windows.compactMap(\.contentView).flatMap(descendants).compactMap { ($0 as? NSTextField)?.stringValue }.contains { $0.contains("当前已是此渠道最新版") }
            let dark = try AppLifecycleUI.shared.offscreenSnapshot(to: output.appendingPathComponent("settings-dark.png"), appearance: .darkAqua, showingUpdateOptions: true)
            checks["updates.real_controls_rendered"] = dark.values.allSatisfy { $0 }
            checks["settings.never_visible_key_or_active"] = !NSApp.isActive && NSApp.windows.allSatisfy { !$0.isVisible && !$0.isKeyWindow } && NSApp.activationPolicy() == .prohibited
            NSApp.windows.forEach { $0.close() }
            checks["settings.close_preserves_hidden_policy"] = !NSApp.isActive && NSApp.windows.allSatisfy { !$0.isVisible }
        } catch { failureMessage = error.localizedDescription; checks["settings.execution_completed"] = false }
        let passed = !checks.isEmpty && checks.values.allSatisfy { $0 }
        let report: JSON = ["schema_version": 1, "checks": checks, "error": failureMessage ?? "", "passed": passed,
                            "scope": "真实设置控件、菜单动作和更新检查；prohibited；未合成输入；未显示窗口或 Dock。"]
        if let data = try? LumenPaths.data(report) { try? data.write(to: output.appendingPathComponent("ui.json"), options: .atomic); print(String(decoding: data, as: UTF8.self)) }
        exit(passed ? 0 : 1)
    }
}
