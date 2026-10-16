import AppKit

@MainActor enum ProductLifecycle {
    static var settings: ProductSettings?
    static var activeConfiguration: AppConfiguration?
    static var shortcuts: AppShortcuts?
    static let updateSource: AppUpdateSource = .github(repository: "zengtianli/lumen-oss")
    static func configuration() -> AppConfiguration {
        AppConfiguration(productID: LumenPaths.bundleID,
                         defaultsKeys: ["library.sidebar", "library.project", "library.tag", "reading.inspector", "appUpdateOptions"] + AppShortcuts.portableKeys,
                         files: [AppConfigurationFile(url: LumenPaths.stateDirectory.appendingPathComponent("settings.json"), keys: ["default_kind", "theme", "content_width", "font_size", "restore_reading", "labels", "generation"])],
                         defaults: LumenPaths.defaults)
    }
    static func product() -> AppLifecycleCLI.Product {
        AppLifecycleCLI.Product(command: "lumen", name: LumenPaths.displayName, configuration: configuration(), updateSource: updateSource)
    }
    static func install(_ engine: EngineClient, refresh: @escaping () -> Void) {
        let shortcuts = AppShortcuts(defaults: LumenPaths.defaults, action: refresh)
        shortcuts.installMenu(); shortcuts.menuItem?.title = "刷新知识库"
        self.shortcuts = shortcuts
        let config = configuration()
        activeConfiguration = config
        let settings = ProductSettings(engine: engine, shortcuts: shortcuts); self.settings = settings
        AppLifecycleUI.install(name: LumenPaths.displayName, configuration: config, updateSource: updateSource)
        AppLifecycleCLI.follow(config)
        AppLifecycleUI.shared.willShow = { settings.refresh() }
        config.onChange = { DispatchQueue.main.async { settings.refresh(); settings.changed?() } }
        AppLifecycleUI.shared.productGroups = {
            settings.groups() + [AppLifecycleUI.group("知识库", rows: [
                AppLifecycleUI.row(T("mac.settings.library", "本机保存"), detail: AppLifecycleUI.detailLabel(LumenPaths.stateDirectory.path)),
                AppLifecycleUI.row(T("mac.settings.connection", "Agent 接入"), detail: AppLifecycleUI.detailLabel(T("mac.settings.connection_detail", "CLI、按需本机 API 与 MCP 使用同一知识库。默认不启动网络服务。"))),
                AppLifecycleUI.row(T("mac.settings.portability", "同步范围"), detail: AppLifecycleUI.detailLabel(T("mac.settings.portability_detail", "iCloud 仅记住可迁移设置；HTML、来源路径、模型凭证和机器权限保留在本机。")))])]
        }
    }
}
