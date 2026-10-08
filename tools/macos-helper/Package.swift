// swift-tools-version: 5.9
//
// WeChatHelper — local-only TCP/HTTP server that reads WeChat's encrypted
// SQLite databases out of the macOS app sandbox and serves them to
// weizx's Python backend over localhost. Solves the TCC limitation
// that blocks Python from accessing com.tencent.xinWeChat containers
// even with Full Disk Access (Python has no bundle ID).
//
// Build & run:
//   cd tools/macos-helper
//   swift build -c release
//   .build/release/WeChatHelper
//
// The first run triggers macOS to ask for Full Disk Access permission.
// Grant it via System Settings → Privacy & Security → Full Disk Access.

import PackageDescription

let package = Package(
    name: "WeChatHelper",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "WeChatHelper", targets: ["WeChatHelper"]),
    ],
    targets: [
        .executableTarget(
            name: "WeChatHelper",
            path: "Sources/WeChatHelper",
            linkerSettings: [
                // Use the system SQLite; we override the key PRAGMA via
                // SQLCipher-compatible bytes. (True SQLCipher 4 is a TODO;
                // for now this is a no-op reader that lists files only.)
                .linkedLibrary("sqlite3"),
            ]
        ),
    ]
)
