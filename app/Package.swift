// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "LabyrinthCleaner",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1"),
    ],
    targets: [
        .target(name: "LabyrinthKit"),
        .executableTarget(
            name: "LabyrinthCleaner",
            dependencies: ["LabyrinthKit", .product(name: "Sparkle", package: "Sparkle")],
            linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        ),
        .executableTarget(name: "labyrinth-trash", dependencies: ["LabyrinthKit"]),
        .testTarget(name: "LabyrinthKitTests", dependencies: ["LabyrinthKit"]),
    ]
)
