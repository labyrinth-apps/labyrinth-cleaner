import Foundation
import Darwin
import LabyrinthKit

func resolvedRealHome() -> String? {
    if let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir {
        return realpathString(String(cString: dir)) ?? String(cString: dir)
    }
    return nil
}

guard let realHome = resolvedRealHome() else {
    // Fail closed: without the real home the helper can't tell a fixture from the real Trash.
    FileHandle.standardError.write("labyrinth-trash: cannot determine the home folder; nothing was moved.\n".data(using: .utf8)!)
    exit(4)
}

let result = runTrashHelper(
    arguments: Array(CommandLine.arguments.dropFirst()),
    realHome: realHome,
    trash: { url in try FileManager.default.trashItem(at: url, resultingItemURL: nil) })
if !result.message.isEmpty {
    FileHandle.standardError.write((result.message + "\n").data(using: .utf8)!)
}
exit(result.code)
