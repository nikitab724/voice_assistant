import SwiftUI
import Combine

struct MeshOrbView: View {
    let state: AssistantState
    let audioLevel: CGFloat
    
    enum AssistantState {
        case idle
        case listening
        case thinking
        case speaking
    }
    
    var body: some View {
        ZStack {
            // Background glow
            Circle()
                .fill(assistantColor.opacity(0.3))
                .blur(radius: isRecording ? 40 : 20)
                .scaleEffect(1.0 + (isRecording ? audioLevel * 0.5 : 0))
            
            // Main button
            Circle()
                .fill(.ultraThinMaterial)
                .frame(width: 180, height: 180)
                .overlay {
                    Circle()
                        .stroke(assistantColor.opacity(0.5), lineWidth: 2)
                }
                .overlay {
                    Image(systemName: iconName)
                        .font(.system(size: 44, weight: .light))
                        .foregroundStyle(assistantColor)
                        .contentTransition(.symbolEffect(.replace))
                }
                .shadow(color: assistantColor.opacity(0.3), radius: 15)
                .scaleEffect(isRecording ? 1.1 + audioLevel * 0.2 : 1.0)
        }
        .frame(width: 280, height: 280)
        .animation(.spring(response: 0.4, dampingFraction: 0.7), value: state)
        .animation(.interactiveSpring, value: audioLevel)
    }
    
    private var assistantColor: Color {
        switch state {
        case .idle: return .blue
        case .listening: return .red
        case .thinking: return .purple
        case .speaking: return .cyan
        }
    }
    
    private var iconName: String {
        if state == .listening { return "mic.fill" }
        if state == .thinking { return "ellipsis" }
        if state == .speaking { return "speaker.wave.3.fill" }
        return "mic"
    }
    
    private var isRecording: Bool { state == .listening }
}

#Preview {
    ZStack {
        Color.black.ignoresSafeArea()
        MeshOrbView(state: .speaking, audioLevel: 0.5)
    }
}
