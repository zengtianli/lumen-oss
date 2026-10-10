import AppKit

/// Only an isolated test can enable this probe. It goes through the production install/follow functions.
@MainActor enum NativeLifecycleProbe {
    private static var timer: Timer?
    static func start(path: String) {
        let url = URL(fileURLWithPath: path)
        do { _ = try AppLifecycleUI.shared.offscreenSnapshot(to: url.deletingPathExtension().appendingPathExtension("png")) }
        catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { record(url) } }
        record(url)
    }
    private static func switches(_ view: NSView) -> [NSSwitch] { ([view as? NSSwitch].compactMap { $0 }) + view.subviews.flatMap(switches) }
    private static func record(_ url: URL) {
        guard let configuration = ProductLifecycle.activeConfiguration else { return }
        let window = NSApp.windows.first { $0.title == LumenPaths.displayName + " 设置" }
        // Product restore-reading comes first; the shared cloud toggle follows it in the configuration card.
        let values = window?.contentView.map { switches($0) } ?? []
        let cloud = values.last
        let data: JSON = ["enabled": configuration.enabled, "switch_on": cloud?.state == .on, "status": configuration.status,
                          "windows_visible": NSApp.windows.filter(\.isVisible).count, "active": NSApp.isActive,
                          "prohibited": NSApp.activationPolicy() == .prohibited]
        if let encoded = try? LumenPaths.data(data) { try? encoded.write(to: url, options: .atomic) }
    }
}
