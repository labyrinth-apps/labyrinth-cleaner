// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "LabyrinthCleaner",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "LabyrinthKit"),
        .executableTarget(name: "LabyrinthCleaner", dependencies: ["LabyrinthKit"]),
        .executableTarget(name: "labyrinth-trash", dependencies: ["LabyrinthKit"]),
        .testTarget(name: "LabyrinthKitTests", dependencies: ["LabyrinthKit"]),
    ]
)
