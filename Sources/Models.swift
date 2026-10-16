import Foundation

typealias JSON = [String: Any]

struct KnowledgeDocument: Equatable {
    let id: String
    var title: String
    var kind: String
    var project: String
    var tags: [String]
    var favorite: Bool
    var archived: Bool
    var trashed: Bool
    var deletedAt: String?
    var version: Int
    var metadataRevision: Int
    var entryPath: String
    var notes: String
    var updatedAt: String
    var pageCount: Int
    var sourcePaths: [String]
    init?(_ value: JSON) {
        guard let id = value["id"] as? String, !id.isEmpty, let title = value["title"] as? String else { return nil }
        self.id = id; self.title = title; kind = value["kind"] as? String ?? "report"
        project = value["project"] as? String ?? ""; tags = value["tags"] as? [String] ?? []
        favorite = value["favorite"] as? Bool ?? false; archived = value["archived"] as? Bool ?? false
        trashed = value["trashed"] as? Bool ?? false; deletedAt = value["deleted_at"] as? String
        version = value["version"] as? Int ?? 1; metadataRevision = value["metadata_revision"] as? Int ?? 0
        entryPath = value["entry_path"] as? String ?? ""
        notes = value["notes"] as? String ?? ""; updatedAt = value["updated_at"] as? String ?? ""
        pageCount = value["page_count"] as? Int ?? 1
        sourcePaths = (value["sources"] as? [JSON] ?? []).compactMap { $0["path"] as? String }
        if let original = value["source_path"] as? String, !original.isEmpty { sourcePaths.removeAll { $0 == original }; sourcePaths.insert(original, at: 0) }
    }
    var kindTitle: String { ["portal": "门户", "report": "阅读与报告", "teach": "教学"][kind] ?? kind }
    var primarySource: String? { sourcePaths.first }
}

struct KnowledgePage: Equatable {
    let id: String, title: String, relativePath: String, absolutePath: String
    init?(_ value: JSON) {
        guard let id = value["id"] as? String, let path = value["absolute_path"] as? String else { return nil }
        self.id = id; title = value["title"] as? String ?? id
        relativePath = value["relative_path"] as? String ?? "index.html"; absolutePath = path
    }
}

struct SearchMatch {
    let documentID: String, pageID: String, title: String, pageTitle: String, anchor: String, snippet: String
    init?(_ value: JSON) {
        guard let id = value["document_id"] as? String else { return nil }
        documentID = id; pageID = value["page_id"] as? String ?? "index"
        title = value["title"] as? String ?? ""; pageTitle = value["page_title"] as? String ?? ""
        anchor = value["anchor"] as? String ?? ""; snippet = value["snippet"] as? String ?? ""
    }
}

struct EngineFailure: LocalizedError {
    let code: String, message: String
    var errorDescription: String? { message }
    init(_ code: String, _ message: String) { self.code = code; self.message = message }
}

enum LumenPaths {
    static let bundleID = "cyou.tianli.Lumen"
    static let displayName = "Lumen · 知页"
    static var stateDirectory: URL {
        if let isolated = ProcessInfo.processInfo.environment["LUMEN_STATE_DIR"], !isolated.isEmpty {
            return URL(fileURLWithPath: isolated, isDirectory: true)
        }
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Lumen", isDirectory: true)
    }
    static var defaults: UserDefaults {
        if let domain = ProcessInfo.processInfo.environment["LUMEN_DEFAULTS_DOMAIN"] { return UserDefaults(suiteName: domain)! }
        return .standard
    }
    static func data(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) }
    static func string(_ value: Any) throws -> String { String(decoding: try data(value), as: UTF8.self) }
}
