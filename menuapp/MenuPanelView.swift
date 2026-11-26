import AppKit
import SwiftUI

enum MenuPanelStatus {
    case running
    case warning
    case error
    case unauthorized

    var color: Color {
        switch self {
        case .running: return Color(red: 0.22, green: 0.85, blue: 0.54)
        case .warning: return Color(red: 1.0, green: 0.68, blue: 0.22)
        case .error: return Color(red: 1.0, green: 0.32, blue: 0.32)
        case .unauthorized: return Color(red: 1.0, green: 0.68, blue: 0.22)
        }
    }
}

struct MenuPanelInfo {
    let headline: String
    let status: MenuPanelStatus
    let last: String?
    let schedule: String?
    let error: String?
}

struct MenuPanelView: View {
    let info: MenuPanelInfo
    let onSettings: () -> Void
    let onOpenLog: () -> Void
    let onQuit: () -> Void

    private let panelWidth: CGFloat = 348

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 9) {
                Circle()
                    .fill(info.status.color)
                    .frame(width: 8, height: 8)
                    .shadow(color: info.status.color.opacity(0.8), radius: 4)
                Text("MAA 监控 · \(info.headline)")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color.white)
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
            .padding(.bottom, 13)

            if let last = info.last {
                HStack(spacing: 0) {
                    Text("上次：")
                        .foregroundStyle(Color.white.opacity(0.55))
                    Text(last)
                        .foregroundStyle(Color.white.opacity(0.92))
                }
                .font(.system(size: 13.5))
                .padding(.horizontal, 18)
                .padding(.vertical, 6)
            }

            if let schedule = info.schedule {
                HStack(spacing: 0) {
                    Text("定时：")
                        .foregroundStyle(Color.white.opacity(0.55))
                    Text(schedule)
                        .foregroundStyle(Color.white.opacity(0.92))
                }
                .font(.system(size: 13.5))
                .padding(.horizontal, 18)
                .padding(.vertical, 6)
            }

            if let error = info.error, !error.isEmpty {
                Text("错误：\(error)")
                    .font(.system(size: 12.5))
                    .foregroundStyle(Color.orange)
                    .lineLimit(2)
                    .padding(.horizontal, 18)
                    .padding(.top, 5)
            }

            Divider()
                .overlay(Color.white.opacity(0.12))
                .padding(.horizontal, 12)
                .padding(.vertical, 10)

            MenuPanelRow(title: "设置…", action: onSettings)
            MenuPanelRow(title: "打开日志", action: onOpenLog)

            Divider()
                .overlay(Color.white.opacity(0.12))
                .padding(.horizontal, 12)
                .padding(.vertical, 10)

            MenuPanelRow(title: "退出", action: onQuit)
                .padding(.bottom, 9)
        }
        .frame(width: panelWidth)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(red: 0.125, green: 0.129, blue: 0.145).opacity(0.97))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Color.white.opacity(0.16), lineWidth: 1)
                )
        )
        .shadow(color: Color.black.opacity(0.45), radius: 28, y: 14)
    }
}

private struct MenuPanelRow: View {
    let title: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack {
                Text(title)
                    .font(.system(size: 13.5))
                    .foregroundStyle(Color.white.opacity(0.95))
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .frame(height: 31)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(hovering ? Color.white.opacity(0.09) : Color.clear)
            )
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 8)
        .padding(.vertical, 1)
        .onHover { hovering = $0 }
    }
}
