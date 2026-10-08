// WeChatHelper — Phase 1 (MVP): HTTP server + /api/db/list endpoint.
//
// Architecture:
//   weizx Python backend  ──HTTP──▶  Swift helper (this binary)
//                                    │
//                                    └─File I/O──▶  ~/Library/Containers/
//                                                       com.tencent.xinWeChat/
//                                                       Data/Documents/xwechat_files/
//
// This MVP only does Phase 1 work:
//   - bind to 127.0.0.1:12345 (localhost only, no network exposure)
//   - GET /api/db/list    → scan container for *.db files
//   - GET /api/health     → liveness check
//   - GET /api/version    → build info
//
// Later phases add /api/db/contacts and /api/db/messages with
// SQLCipher decryption.

import Foundation
import Network

// MARK: - Configuration

// FDA-aware path resolution:
// 1. If WeChatHelper is launched from a properly-bundled .app with FDA,
//    it reads the WeChat sandbox directly.
// 2. Otherwise (CLI binary, dev environment), fall back to a non-sandbox
//    copy the user made manually. This is the test workflow.
func resolveDBRoot() -> String {
    if let override = ProcessInfo.processInfo.environment["WEIXX_DB_ROOT"] {
        return (override as NSString).expandingTildeInPath
    }
    let sandbox = ("~/Library/Containers/com.tencent.xinWeChat/"
                  + "Data/Documents/xwechat_files/" as NSString)
        .expandingTildeInPath
    if FileManager.default.isReadableFile(atPath: sandbox) {
        return sandbox
    }
    // Fallback: non-sandbox copy the user can populate without FDA
    let fallback = ("~/xwechat_files/" as NSString).expandingTildeInPath
    return fallback
}

let WEIX_DB_ROOT = resolveDBRoot()

let LISTEN_HOST = "127.0.0.1"
let LISTEN_PORT: UInt16 = 12345

// MARK: - Tiny HTTP parser
//
// We only need to handle tiny GET requests for /api/*. We do a manual
// parse to avoid pulling in a server framework. Returns (method, path,
// queryString, body) or nil on malformed input.

struct HTTPRequest {
    let method: String
    let path: String
    let query: [String: String]
    let body: String
}

func parseHTTPRequest(_ data: Data) -> HTTPRequest? {
    guard let raw = String(data: data, encoding: .utf8) else { return nil }
    let lines = raw.split(separator: "\r\n", omittingEmptySubsequences: false)
    guard let requestLine = lines.first else { return nil }
    let parts = requestLine.split(separator: " ", omittingEmptySubsequences: true)
    guard parts.count >= 2 else { return nil }
    let method = String(parts[0])
    let rawPath = String(parts[1])

    // Split path and query
    var path = rawPath
    var query: [String: String] = [:]
    if let qIdx = rawPath.firstIndex(of: "?") {
        path = String(rawPath[..<qIdx])
        let qs = String(rawPath[rawPath.index(after: qIdx)...])
        for pair in qs.split(separator: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1)
            if kv.count == 2 {
                let k = String(kv[0]).removingPercentEncoding ?? String(kv[0])
                let v = String(kv[1]).removingPercentEncoding ?? String(kv[1])
                query[k] = v
            }
        }
    }

    // Body is after the blank line
    let bodyStart = raw.range(of: "\r\n\r\n")?.upperBound
    let body = bodyStart.map { String(raw[$0...]) } ?? ""

    return HTTPRequest(method: method, path: path, query: query, body: body)
}

// MARK: - Response helpers

func makeResponse(status: String, body: String, contentType: String = "application/json") -> Data {
    let payload = body.data(using: .utf8) ?? Data()
    var header = "HTTP/1.1 \(status)\r\n"
    header += "Content-Type: \(contentType); charset=utf-8\r\n"
    header += "Content-Length: \(payload.count)\r\n"
    header += "Connection: close\r\n"
    header += "Cache-Control: no-store\r\n"
    header += "\r\n"
    var out = Data(header.utf8)
    out.append(payload)
    return out
}

func jsonOK(_ dict: [String: Any]) -> Data {
    guard let data = try? JSONSerialization.data(
        withJSONObject: dict, options: [.prettyPrinted, .sortedKeys]
    ) else {
        return makeResponse(status: "500 Internal Server Error", body: "{\"error\":\"json_encode_failed\"}")
    }
    let s = String(data: data, encoding: .utf8) ?? "{}"
    return makeResponse(status: "200 OK", body: s)
}

func errorResponse(status: String, message: String) -> Data {
    return makeResponse(status: status, body: "{\"error\":\"\(message)\"}")
}

// MARK: - Handlers

func listDatabases() -> [[String: String]] {
    let fm = FileManager.default
    var results: [[String: String]] = []
    let log = { (msg: String) in
        FileHandle.standardError.write("[wch] \(msg)\n".data(using: .utf8) ?? Data())
    }
    log("scanning \(WEIX_DB_ROOT)")
    var isDir: ObjCBool = false
    if !fm.fileExists(atPath: WEIX_DB_ROOT, isDirectory: &isDir) || !isDir.boolValue {
        log("root does not exist or is not dir")
        return []
    }
    log("root exists and is dir, listing...")
    guard let wxids = try? fm.contentsOfDirectory(atPath: WEIX_DB_ROOT) else {
        log("contentsOfDirectory(WEIX_DB_ROOT) failed: \(WEIX_DB_ROOT)")
        return []
    }
    log("found \(wxids.count) entries: \(wxids.prefix(5))")
    for wxid in wxids.sorted() where wxid.hasPrefix("wxid_") {
        let userPath = WEIX_DB_ROOT + "/" + wxid
        let dbStorage = userPath + "/db_storage"
        if !fm.fileExists(atPath: dbStorage, isDirectory: &isDir) || !isDir.boolValue {
            log("skip \(wxid): no db_storage at \(dbStorage) (isDir=\(isDir.boolValue))")
            continue
        }
        log("\(wxid): db_storage OK, scanning categories")
        guard let cats = try? fm.contentsOfDirectory(atPath: dbStorage) else { continue }
        log("\(wxid): \(cats.count) categories: \(cats.prefix(8))")
        for cat in cats.sorted() {
            let catPath = dbStorage + "/" + cat
            if !fm.fileExists(atPath: catPath, isDirectory: &isDir) || !isDir.boolValue {
                continue
            }
            guard let files = try? fm.contentsOfDirectory(atPath: catPath) else { continue }
            for f in files.sorted() where f.hasSuffix(".db") {
                let fullPath = catPath + "/" + f
                results.append([
                    "wxid": wxid,
                    "category": cat,
                    "filename": f,
                    "path": fullPath,
                ])
            }
        }
    }
    log("total DBs: \(results.count)")
    return results
}

func handle(_ req: HTTPRequest) -> Data {
    switch req.path {
    case "/api/health":
        return jsonOK([
            "status": "ok",
            "version": "0.1.0-mvp",
            "platform": "macos",
            "db_root": WEIX_DB_ROOT,
        ])
    case "/api/version":
        return jsonOK([
            "name": "WeChatHelper",
            "version": "0.1.0-mvp",
            "phase": "1 — list only (no SQLCipher yet)",
        ])
    case "/api/db/list":
        let dbs = listDatabases()
        return jsonOK([
            "count": dbs.count,
            "databases": dbs,
        ])
    default:
        return errorResponse(status: "404 Not Found",
                            message: "no handler for \(req.path)")
    }
}

// MARK: - Server loop

let params = NWParameters.tcp
let listener = try! NWListener(using: params, on: NWEndpoint.Port(rawValue: LISTEN_PORT)!)
listener.newConnectionHandler = { conn in
    conn.start(queue: .global())
    conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { data, _, _, _ in
        guard let data = data, let req = parseHTTPRequest(data) else {
            conn.send(content: errorResponse(status: "400 Bad Request",
                                            message: "malformed"),
                     completion: .contentProcessed { _ in conn.cancel() })
            return
        }
        FileHandle.standardError.write(
            "[\(Date())] \(req.method) \(req.path)\n".data(using: .utf8) ?? Data()
        )
        let resp = handle(req)
        conn.send(content: resp,
                 completion: .contentProcessed { _ in conn.cancel() })
    }
}
listener.stateUpdateHandler = { state in
    switch state {
    case .ready:
        FileHandle.standardError.write(
            "WeChatHelper listening on \(LISTEN_HOST):\(LISTEN_PORT)\n"
                .data(using: .utf8) ?? Data()
        )
    case .failed(let err):
        FileHandle.standardError.write(
            "WeChatHelper failed: \(err)\n".data(using: .utf8) ?? Data()
        )
    default:
        break
    }
}
listener.start(queue: .global())

// Run forever
dispatchMain()
