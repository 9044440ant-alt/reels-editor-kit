// Находит лицо на кадрах видео через Apple Vision.
// usage: faces <video> <step_seconds>
// stdout: JSON [{"t": сек, "face": [x, y, w, h] | null}] — доли кадра, начало координат сверху слева.
import AVFoundation
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count >= 3, let step = Double(args[2]) else {
    FileHandle.standardError.write("usage: faces <video> <step>\n".data(using: .utf8)!)
    exit(2)
}
let asset = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = CMTime(seconds: 0.2, preferredTimescale: 600)
gen.requestedTimeToleranceAfter = CMTime(seconds: 0.2, preferredTimescale: 600)
gen.maximumSize = CGSize(width: 720, height: 1280)

let duration = CMTimeGetSeconds(asset.duration)
var out: [[String: Any]] = []
var t = 0.2
while t < duration {
    var item: [String: Any] = ["t": (t * 100).rounded() / 100]
    if let img = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) {
        let req = VNDetectFaceRectanglesRequest()
        try? VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
        if let best = (req.results ?? []).max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }) {
            let b = best.boundingBox
            item["face"] = [b.minX, 1 - b.maxY, b.width, b.height].map { ($0 * 1000).rounded() / 1000 }
        } else {
            item["face"] = NSNull()
        }
    }
    out.append(item)
    t += step
}
let data = try! JSONSerialization.data(withJSONObject: out)
print(String(data: data, encoding: .utf8)!)
