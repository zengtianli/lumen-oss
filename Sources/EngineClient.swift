import Foundation

/// All GUI actions run through the same shipped RPC dispatcher as CLI/API/MCP.
struct EngineClient: Sendable {
    let stateDirectory: URL
    init(stateDirectory: URL = LumenPaths.stateDirectory) { self.stateDirectory = stateDirectory }

    static func launcher() throws -> (URL, [String]) {
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        let resources = executable.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Resources")
        let frozen = resources.appendingPathComponent("engine/lumen-engine")
        if FileManager.default.isExecutableFile(atPath: frozen.path) { return (frozen, []) }
        if let path = ProcessInfo.processInfo.environment["LUMEN_ENGINE"], FileManager.default.isExecutableFile(atPath: path) {
            return (URL(fileURLWithPath: path), [])
        }
        // Development only; a .app must always use its own frozen engine.
        guard !executable.path.contains(".app/Contents/") else {
            throw EngineFailure("engine_missing", "安装包中的知识引擎缺失，请重新安装 Lumen。")
        }
        let root = executable.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let source = root.appendingPathComponent("engine/entry.py")
        guard FileManager.default.fileExists(atPath: source.path) else { throw EngineFailure("engine_missing", "找不到开发引擎；请构建完整 Lumen.app。") }
        if let python = ProcessInfo.processInfo.environment["LUMEN_PYTHON"] { return (URL(fileURLWithPath: python), [source.path]) }
        return (URL(fileURLWithPath: "/usr/bin/env"), ["python3", source.path])
    }

    func call(_ action: String, _ params: JSON = [:], timeout: TimeInterval = 60) async throws -> JSON {
        // Large HTML/image snapshots exceed ARG_MAX. Private payload files also keep content out of ps output.
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("lumen-rpc-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: folder) }
        let payload = folder.appendingPathComponent("params.json")
        try LumenPaths.data(params).write(to: payload, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: payload.path)
        let words = ["rpc", action, "--params", payload.path, "--json", "--state-dir", stateDirectory.path]
        let data = try await Task.detached(priority: .userInitiated) { try Self.capture(words, timeout: timeout).0 }.value
        guard let envelope = try JSONSerialization.jsonObject(with: data) as? JSON else { throw EngineFailure("invalid_response", "知识引擎返回了无法读取的结果。") }
        guard envelope["ok"] as? Bool == true else {
            let error = envelope["error"] as? JSON ?? [:]
            throw EngineFailure(error["code"] as? String ?? "failed", error["message"] as? String ?? "操作未完成，原有内容保留。")
        }
        return envelope["result"] as? JSON ?? [:]
    }

    static func capture(_ words: [String], timeout: TimeInterval = 60) throws -> (Data, Int32) {
        let (url, prefix) = try launcher()
        let child = Process(); child.executableURL = url; child.arguments = prefix + words
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PATH"] = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin").path + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (environment["PATH"] ?? "")
        child.environment = environment
        let output = Pipe(), errors = Pipe(); child.standardOutput = output; child.standardError = errors; child.standardInput = FileHandle.nullDevice
        try child.run()
        // Drain both pipes while running; stderr is never mixed into the RPC JSON.
        let group = DispatchGroup(), lock = NSLock()
        var stdout = Data(), stderr = Data()
        group.enter(); DispatchQueue.global(qos: .userInitiated).async { let data = output.fileHandleForReading.readDataToEndOfFile(); lock.lock(); stdout = data; lock.unlock(); group.leave() }
        group.enter(); DispatchQueue.global(qos: .utility).async { let data = errors.fileHandleForReading.readDataToEndOfFile(); lock.lock(); stderr = data; lock.unlock(); group.leave() }
        let deadline = Date().addingTimeInterval(timeout)
        while child.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.025) }
        if child.isRunning {
            child.terminate(); let grace = Date().addingTimeInterval(1)
            while child.isRunning && Date() < grace { Thread.sleep(forTimeInterval: 0.025) }
            if child.isRunning { kill(child.processIdentifier, SIGKILL) }
            throw EngineFailure("timeout", "知识引擎未在期限内返回。请查询任务状态，原有内容保留。")
        }
        child.waitUntilExit(); group.wait()
        if stdout.isEmpty && child.terminationStatus != 0 { throw EngineFailure("engine_failed", String(decoding: stderr, as: UTF8.self)) }
        return (stdout, child.terminationStatus)
    }

    /// CLI passthrough keeps exact stdout/stderr/status without starting NSApplication.
    static func relay(_ words: [String]) -> Int32 {
        do {
            let (url, prefix) = try launcher(); let child = Process()
            child.executableURL = url; child.arguments = prefix + words
            child.standardInput = FileHandle.standardInput; child.standardOutput = FileHandle.standardOutput; child.standardError = FileHandle.standardError
            try child.run(); child.waitUntilExit(); return child.terminationStatus
        } catch {
            let json: JSON = ["ok": false, "error": ["code": "engine_missing", "message": error.localizedDescription]]
            if words.contains("--json"), let string = try? LumenPaths.string(json) { print(string) }
            else { fputs(error.localizedDescription + "\n", stderr) }
            return 1
        }
    }
}
