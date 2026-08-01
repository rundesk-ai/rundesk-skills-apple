import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("error: \(message)\n".utf8))
    exit(1)
}

guard CommandLine.arguments.count == 5 else {
    fail("usage: ApplePermissionLauncher <worker> <request.json> <response.json> <error.txt>")
}

let worker = Process()
worker.executableURL = URL(fileURLWithPath: CommandLine.arguments[1])
worker.arguments = Array(CommandLine.arguments[2...4])

do {
    try worker.run()
    worker.waitUntilExit()
} catch {
    fail("unable to run Apple integration worker: \(error.localizedDescription)")
}

exit(worker.terminationStatus)
