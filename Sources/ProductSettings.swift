import AppKit

/// The settings window reads/writes the same engine settings.json as every other interface.
@MainActor final class ProductSettings: NSObject {
    let engine: EngineClient
    var changed: (() -> Void)?
    let kind = NSPopUpButton(), theme = NSPopUpButton(), font = NSPopUpButton(), timeout = NSPopUpButton()
    let restore = NSSwitch(), detail = AppLifecycleUI.detailLabel()
    private var loading = false
    init(engine: EngineClient) {
        self.engine = engine; super.init()
        kind.addItems(withTitles: ["门户", "阅读与报告", "教学"])
        theme.addItems(withTitles: ["亮色", "跟随系统", "深色"])
        font.addItems(withTitles: (12...30).map { "\($0) pt" })
        timeout.addItems(withTitles: ["2 分钟", "5 分钟", "10 分钟", "15 分钟", "20 分钟"])
        for control in [kind, theme, font, timeout, restore] as [NSControl] { control.target = self; control.action = #selector(save(_:)) }
        for popup in [kind, theme, font, timeout] { popup.widthAnchor.constraint(equalToConstant: 145).isActive = true }
    }
    func groups() -> [NSView] {
        [AppLifecycleUI.group("生成与阅读", rows: [
            AppLifecycleUI.row("默认生成用途", detail: AppLifecycleUI.detailLabel("新知识沿用这一用途，也可以在生成时选择。"), accessory: kind),
            AppLifecycleUI.row("阅读外观", detail: AppLifecycleUI.detailLabel("默认亮色；修改后作用于当前阅读器。"), accessory: theme),
            AppLifecycleUI.row("正文字号", accessory: font),
            AppLifecycleUI.row("恢复阅读位置", detail: AppLifecycleUI.detailLabel("再次打开时接着上次阅读。"), accessory: restore),
            AppLifecycleUI.row("生成时限", detail: AppLifecycleUI.detailLabel("生成任务在期限内完成或返回明确状态。"), accessory: timeout),
            AppLifecycleUI.row("设置状态", detail: detail)
        ])]
    }
    func refresh() {
        Task { await load() }
    }
    func load() async {
            do {
                let result = try await engine.call("settings"); let settings = result["settings"] as? JSON ?? [:]
                loading = true
                kind.selectItem(at: ["portal", "report", "teach"].firstIndex(of: settings["default_kind"] as? String ?? "portal") ?? 0)
                theme.selectItem(at: ["light", "system", "dark"].firstIndex(of: settings["theme"] as? String ?? "light") ?? 0)
                font.selectItem(at: max(0, min(18, (settings["font_size"] as? Int ?? 17) - 12)))
                restore.state = settings["restore_reading"] as? Bool == false ? .off : .on
                timeout.selectItem(at: [120, 300, 600, 900, 1200].firstIndex(of: (settings["generation"] as? JSON)?["timeout_seconds"] as? Int ?? 600) ?? 2)
                loading = false; detail.stringValue = result["warning"] as? String ?? "与 CLI、API、MCP 使用同一份标准与设置。"; detail.textColor = .secondaryLabelColor
            } catch { detail.stringValue = error.localizedDescription; detail.textColor = .systemOrange }
    }
    @objc func save(_ sender: NSControl) {
        guard !loading else { return }
        let values: JSON
        switch sender {
        case kind: values = ["default_kind": ["portal", "report", "teach"][kind.indexOfSelectedItem]]
        case theme: values = ["theme": ["light", "system", "dark"][theme.indexOfSelectedItem]]
        case font: values = ["font_size": 12 + font.indexOfSelectedItem]
        case restore: values = ["restore_reading": restore.state == .on]
        case timeout: values = ["generation": ["timeout_seconds": [120, 300, 600, 900, 1200][timeout.indexOfSelectedItem]]]
        default: return
        }
        Task {
            do { _ = try await engine.call("settings", ["values": values]); detail.stringValue = "设置已保存"; detail.textColor = .secondaryLabelColor; changed?() }
            catch { detail.stringValue = error.localizedDescription; detail.textColor = .systemOrange; refresh() }
        }
    }
}
