//
//  ContentView.swift
//  voice assistant
//
//  Created by Nikita Borisov on 12/6/25.
//

import SwiftUI
import SwiftData
import Combine
import AVFoundation
import Speech
import FirebaseAuth
import CoreLocation

// MARK: - Data Models

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    let text: String
    let isUser: Bool
    let timestamp = Date()
}

// MARK: - Location Manager

class LocationManager: NSObject, ObservableObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    @Published var location: CLLocation?
    @Published var authorizationStatus: CLAuthorizationStatus = .notDetermined
    
    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyKilometer // City-level is fine for weather
    }
    
    func requestPermission() {
        manager.requestWhenInUseAuthorization()
    }
    
    func requestLocation() {
        manager.requestLocation()
    }
    
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        location = locations.last
    }
    
    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        print("[Location] Error: \(error.localizedDescription)")
    }
    
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorizationStatus = manager.authorizationStatus
        if authorizationStatus == .authorizedWhenInUse || authorizationStatus == .authorizedAlways {
            manager.requestLocation()
        }
    }
}

// MARK: - Chat Client

class ChatClient {
    private let baseURL = "http://192.168.1.169:5050"
    
    struct ChatRequest: Encodable {
        let session_id: String
        let message: String
        let user_id: String?
        let google_access_token: String?
        let allowed_tool_names: [String]?
        let allowed_tool_tags: [String]?
        let timezone_name: String?
        let user_latitude: Double?
        let user_longitude: Double?
    }
    
    struct ChatResponse: Decodable {
        let text: String?
        let tool_calls: [ToolCall]?
        let audio: String?        // Base64 encoded audio
        let audio_format: String? // e.g. "mp3"
        let error: String?        // Error message from server
        
        struct ToolCall: Decodable {
            let name: String?
            let arguments: [String: AnyCodable]?
            let response: String?
        }
    }
    
    // Helper to decode any JSON value
    struct AnyCodable: Decodable {
        let value: Any
        
        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let string = try? container.decode(String.self) {
                value = string
            } else if let int = try? container.decode(Int.self) {
                value = int
            } else if let double = try? container.decode(Double.self) {
                value = double
            } else if let bool = try? container.decode(Bool.self) {
                value = bool
            } else if container.decodeNil() {
                value = NSNull()
            } else {
                value = ""
            }
        }
    }
    
    struct APIError: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    // MARK: - Tools list (for UI)
    struct ToolInfo: Decodable, Identifiable, Hashable {
        let name: String
        let description: String
        let tags: [String]
        var id: String { name }
    }
    
    struct ToolsResponse: Decodable {
        let tools: [ToolInfo]
        let error: String?
    }

    struct SendDraftRequest: Encodable {
        let draft_id: String
        let user_id: String?
        let google_access_token: String?
    }

    struct SendDraftResponse: Decodable {
        let status: String?
        let draftId: String?
        let messageId: String?
        let threadId: String?
        let error: String?
    }
    
    func fetchTools() async throws -> [ToolInfo] {
        guard let url = URL(string: "\(baseURL)/api/tools") else {
            throw URLError(.badURL)
        }
        let (data, response) = try await URLSession.shared.data(from: url)
        let httpResponse = response as? HTTPURLResponse
        let decoded = try JSONDecoder().decode(ToolsResponse.self, from: data)
        if let error = decoded.error {
            throw APIError(message: error)
        }
        if let status = httpResponse?.statusCode, !(200...299).contains(status) {
            throw APIError(message: "Server returned status \(status)")
        }
        return decoded.tools
    }

    func sendGmailDraft(draftId: String, userId: String?, googleAccessToken: String?) async throws -> SendDraftResponse {
        guard let url = URL(string: "\(baseURL)/api/gmail/draft/send") else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body = SendDraftRequest(draft_id: draftId, user_id: userId, google_access_token: googleAccessToken)
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await URLSession.shared.data(for: request)
        let httpResponse = response as? HTTPURLResponse
        let decoded = try JSONDecoder().decode(SendDraftResponse.self, from: data)
        if let error = decoded.error {
            throw APIError(message: error)
        }
        if let status = httpResponse?.statusCode, !(200...299).contains(status) {
            throw APIError(message: "Server returned status \(status)")
        }
        return decoded
    }
    
    // MARK: - Streaming Events
    
    enum StreamEvent {
        case textDelta(String)
        case toolCall(name: String, arguments: [String: Any])
        case toolResult(name: String, result: String)
        case audio(base64: String, format: String)
        case done(fullText: String)
        case error(String)
    }
    
    func sendMessage(
        sessionId: String,
        message: String,
        userId: String?,
        googleAccessToken: String?,
        allowedToolNames: [String]?,
        allowedToolTags: [String]?,
        timezoneName: String?,
        userLatitude: Double? = nil,
        userLongitude: Double? = nil
    ) async throws -> ChatResponse {
        guard let url = URL(string: "\(baseURL)/api/chat") else {
            throw URLError(.badURL)
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        let body = ChatRequest(
            session_id: sessionId,
            message: message,
            user_id: userId,
            google_access_token: googleAccessToken,
            allowed_tool_names: allowedToolNames,
            allowed_tool_tags: allowedToolTags,
            timezone_name: timezoneName,
            user_latitude: userLatitude,
            user_longitude: userLongitude
        )
        request.httpBody = try JSONEncoder().encode(body)
        
        let (data, response) = try await URLSession.shared.data(for: request)
        let httpResponse = response as? HTTPURLResponse
        
        // Try to decode response (works for both success and error)
        let decoded = try JSONDecoder().decode(ChatResponse.self, from: data)
        
        // Check for error in response
        if let error = decoded.error {
            throw APIError(message: error)
        }
        
        // Check HTTP status
        if let status = httpResponse?.statusCode, !(200...299).contains(status) {
            throw APIError(message: "Server returned status \(status)")
        }
        
        return decoded
    }
    
    // MARK: - Streaming API
    
    func sendMessageStreaming(
        sessionId: String,
        message: String,
        userId: String?,
        googleAccessToken: String?,
        allowedToolNames: [String]?,
        allowedToolTags: [String]?,
        timezoneName: String?,
        userLatitude: Double? = nil,
        userLongitude: Double? = nil
    ) -> AsyncThrowingStream<StreamEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    guard let url = URL(string: "\(baseURL)/api/chat/stream") else {
                        continuation.finish(throwing: URLError(.badURL))
                        return
                    }
                    
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    // Streaming requests can be long-lived; avoid default timeouts.
                    request.timeoutInterval = 3600
                    
                    let body = ChatRequest(
                        session_id: sessionId,
                        message: message,
                        user_id: userId,
                        google_access_token: googleAccessToken,
                        allowed_tool_names: allowedToolNames,
                        allowed_tool_tags: allowedToolTags,
                        timezone_name: timezoneName,
                        user_latitude: userLatitude,
                        user_longitude: userLongitude
                    )
                    request.httpBody = try JSONEncoder().encode(body)
                    
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse,
              (200...299).contains(httpResponse.statusCode) else {
                        continuation.finish(throwing: APIError(message: "Server error"))
                        return
                    }
                    
                    var currentEvent = ""
                    var currentData = ""
                    
                    for try await line in bytes.lines {
                        if line.hasPrefix("event: ") {
                            currentEvent = String(line.dropFirst(7))
                        } else if line.hasPrefix("data: ") {
                            currentData = String(line.dropFirst(6))
                            
                            // Process the event
                            if let event = parseSSEEvent(event: currentEvent, data: currentData) {
                                continuation.yield(event)
                                
                                // Finish on done or error
                                if case .done = event {
                                    continuation.finish()
                                    return
                                }
                            }
                            
                            currentEvent = ""
                            currentData = ""
                        }
                    }
                    
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
    
    private func parseSSEEvent(event: String, data: String) -> StreamEvent? {
        guard let jsonData = data.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] else {
            return nil
        }
        
        switch event {
        case "text_delta":
            if let text = json["text"] as? String {
                return .textDelta(text)
            }
        case "tool_call":
            if let name = json["name"] as? String,
               let arguments = json["arguments"] as? [String: Any] {
                return .toolCall(name: name, arguments: arguments)
            }
        case "tool_result":
            if let name = json["name"] as? String,
               let result = json["result"] as? String {
                return .toolResult(name: name, result: result)
            }
        case "audio":
            if let audio = json["audio"] as? String,
               let format = json["format"] as? String {
                return .audio(base64: audio, format: format)
            }
        case "done":
            let fullText = json["full_text"] as? String ?? ""
            return .done(fullText: fullText)
        case "error":
            let message = json["message"] as? String ?? "Unknown error"
            return .error(message)
        default:
            break
        }
        return nil
    }
}

// MARK: - Audio Queue Player

class AudioQueuePlayer: NSObject, AVAudioPlayerDelegate, ObservableObject {
    private var audioQueue: [Data] = []
    private var currentPlayer: AVAudioPlayer?
    private var isPlaying = false
    @Published private(set) var isSpeaking = false
    
    private func setSpeaking(_ speaking: Bool) {
        guard isSpeaking != speaking else { return }
        if Thread.isMainThread {
            isSpeaking = speaking
        } else {
            DispatchQueue.main.async { [weak self] in
                self?.isSpeaking = speaking
            }
        }
    }
    
    func enqueue(audioData: Data) {
        audioQueue.append(audioData)
        playNextIfNeeded()
    }
    
    func stopAll() {
        audioQueue.removeAll()
        currentPlayer?.stop()
        currentPlayer = nil
        isPlaying = false
        setSpeaking(false)
        print("[AudioQueue] Stopped and cleared queue")
    }
    
    private func playNextIfNeeded() {
        guard !isPlaying, !audioQueue.isEmpty else { return }
        
        let audioData = audioQueue.removeFirst()
        
        do {
            let session = AVAudioSession.sharedInstance()
            // Use .spokenAudio mode to keep playback volume consistent and high quality
            // while the microphone is active in continuous mode.
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetoothA2DP])
            try session.setActive(true)
            
            currentPlayer = try AVAudioPlayer(data: audioData)
            currentPlayer?.delegate = self
            currentPlayer?.prepareToPlay()
            currentPlayer?.play()
            isPlaying = true
            setSpeaking(true)
            print("[AudioQueue] Playing chunk, \(audioQueue.count) remaining in queue")
        } catch {
            print("[AudioQueue] Playback error: \(error)")
            isPlaying = false
            setSpeaking(false)
            playNextIfNeeded()  // Try next chunk
        }
    }
    
    // AVAudioPlayerDelegate
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        isPlaying = false
        setSpeaking(false)
        print("[AudioQueue] Chunk finished, success: \(flag)")
        playNextIfNeeded()
    }
    
    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        print("[AudioQueue] Decode error: \(error?.localizedDescription ?? "unknown")")
        isPlaying = false
        setSpeaking(false)
        playNextIfNeeded()
    }
}

// MARK: - Waveform View

struct RecordingWaveformView: View {
    let levels: [CGFloat]
    var color: Color = .red
    var scale: CGFloat = 30
    
    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<levels.count, id: \.self) { index in
                RoundedRectangle(cornerRadius: 2)
                    .fill(color)
                    .frame(width: 4, height: max(4, levels[index] * scale))
                    .animation(.easeInOut(duration: 0.05), value: levels[index])
            }
        }
    }
}

// MARK: - Typing Indicator

struct TypingIndicatorView: View {
    @State private var dotOpacities: [Double] = [0.3, 0.3, 0.3]
    
    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Color.gray)
                    .frame(width: 8, height: 8)
                    .opacity(dotOpacities[index])
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Color(.systemGray5))
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .onAppear {
            animateDots()
        }
    }
    
    private func animateDots() {
        for i in 0..<3 {
            withAnimation(
                Animation.easeInOut(duration: 0.4)
                    .repeatForever(autoreverses: true)
                    .delay(Double(i) * 0.15)
            ) {
                dotOpacities[i] = 1.0
            }
        }
    }
}

// MARK: - Scroll Tracking (Auto-scroll only if user is already at bottom)

private struct ScrollViewHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct BottomMarkerYKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - Tool Drawer

struct ToolDrawerView: View {
    @Binding var isOpen: Bool
    @Binding var calendarEnabled: Bool
    @Binding var gmailEnabled: Bool
    @Binding var tasksEnabled: Bool
    @Binding var weatherEnabled: Bool
    @Binding var timezoneName: String
    @Binding var isContinuousMode: Bool
    let availableTools: [ChatClient.ToolInfo]
    @Binding var enabledToolNames: Set<String>
    let refreshTools: () -> Void
    
    @State private var calendarExpanded = true
    @State private var gmailExpanded = true
    @State private var tasksExpanded = true
    @State private var weatherExpanded = true
    @State private var otherExpanded = false
    
    private let timezones: [(label: String, value: String)] = [
        ("Chicago (CT)", "America/Chicago"),
        ("New York (ET)", "America/New_York"),
        ("Los Angeles (PT)", "America/Los_Angeles"),
        ("UTC", "UTC"),
    ]
    
    var body: some View {
        GeometryReader { geo in
            let safeTop = geo.safeAreaInsets.top
            HStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        Text("Settings")
                            .font(.title2)
                            .bold()
                        Spacer()
                        Button {
                            withAnimation(.spring(response: 0.3, dampingFraction: 0.9)) {
                                isOpen = false
                            }
                        } label: {
                            Image(systemName: "xmark")
                                .foregroundStyle(.secondary)
                        }
                    }
                    
                    // Timezone selector (same section as tools)
                    Menu {
                        ForEach(timezones, id: \.value) { tz in
                            Button {
                                timezoneName = tz.value
                            } label: {
                                if timezoneName == tz.value {
                                    Label(tz.label, systemImage: "checkmark")
                                } else {
                                    Text(tz.label)
                                }
                            }
                        }
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "globe")
                            Text("Timezone")
                            Spacer()
                            Text(timezoneName)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        .padding(.vertical, 10)
                        .padding(.horizontal, 12)
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    
                    Text("Tools")
                        .font(.headline)
                        .padding(.top, 8)
                    
                    Toggle("Calendar", isOn: $calendarEnabled)
                    Toggle("Gmail", isOn: $gmailEnabled)
                    Toggle("Tasks", isOn: $tasksEnabled)
                    Toggle("Weather", isOn: $weatherEnabled)
                    Toggle("Continuous Voice", isOn: $isContinuousMode)
                        .padding(.top, 4)
                    
                    Button {
                        refreshTools()
                    } label: {
                        Label("Refresh tools list", systemImage: "arrow.clockwise")
                    }
                    .font(.subheadline)
                    .padding(.top, 4)

                    // Per-tool selection (filtered by enabled services)
                    ScrollView {
                        VStack(alignment: .leading, spacing: 10) {
                            toolDisclosureSection(
                                title: "Calendar",
                                tag: "calendar",
                                enabled: calendarEnabled,
                                isExpanded: $calendarExpanded
                            )
                            
                            toolDisclosureSection(
                                title: "Gmail",
                                tag: "gmail",
                                enabled: gmailEnabled,
                                isExpanded: $gmailExpanded
                            )
                            
                            toolDisclosureSection(
                                title: "Tasks",
                                tag: "tasks",
                                enabled: tasksEnabled,
                                isExpanded: $tasksExpanded
                            )
                            
                            toolDisclosureSection(
                                title: "Weather",
                                tag: "weather",
                                enabled: weatherEnabled,
                                isExpanded: $weatherExpanded
                            )

                            let otherTools = availableTools.filter { tool in
                                let tags = Set(tool.tags)
                                return !tags.contains("calendar") && !tags.contains("gmail") && !tags.contains("tasks") && !tags.contains("weather")
                            }
                            if !otherTools.isEmpty {
                                DisclosureGroup(isExpanded: $otherExpanded) {
                                    VStack(alignment: .leading, spacing: 10) {
                                        ForEach(otherTools) { tool in
                                            Toggle(isOn: Binding(
                                                get: { enabledToolNames.contains(tool.name) },
                                                set: { on in
                                                    if on { enabledToolNames.insert(tool.name) } else { enabledToolNames.remove(tool.name) }
                                                }
                                            )) {
                                                VStack(alignment: .leading, spacing: 2) {
                                                    Text(tool.name)
                                                        .font(.subheadline)
                                                    if !tool.description.isEmpty {
                                                        Text(tool.description)
                                                            .font(.caption)
                                                            .foregroundStyle(.secondary)
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    .padding(.top, 8)
                                } label: {
                                    HStack(spacing: 8) {
                                        Image(systemName: "wrench.and.screwdriver")
                                        Text("Other")
                                            .font(.subheadline)
                                            .fontWeight(.semibold)
                                        Spacer()
                                        Text("\(otherTools.count)")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    .padding(.vertical, 10)
                                    .padding(.horizontal, 12)
                                    .background(Color(.systemGray6).opacity(0.6))
                                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                                }
                            }
                        }
                        .padding(.top, 6)
                    }
                    
                    Text("Tip: turn off tools you don’t want the assistant to use right now.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    
                    Spacer()
                }
                // Push down so we don't cover the status bar time
                .padding(.top, safeTop + 12)
                .padding(.horizontal, 16)
                .padding(.bottom, 16)
                .frame(width: 300)
                .frame(maxHeight: .infinity)
                .background(.ultraThinMaterial)
                .overlay(
                    RoundedRectangle(cornerRadius: 0)
                        .stroke(Color.white.opacity(0.08), lineWidth: 1)
                )
                .shadow(radius: 8)
                .offset(x: isOpen ? 0 : -340)
                .animation(.spring(response: 0.3, dampingFraction: 0.9), value: isOpen)
                
                Spacer()
            }
            .ignoresSafeArea()
            .allowsHitTesting(isOpen)
        }
    }
    
    private func toolDisclosureSection(
        title: String,
        tag: String,
        enabled: Bool,
        isExpanded: Binding<Bool>
    ) -> some View {
        let tools = availableTools.filter { $0.tags.contains(tag) }
        return Group {
            if !tools.isEmpty {
                DisclosureGroup(isExpanded: isExpanded) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Spacer()
                            Button(enabled ? "Select none" : "Disabled") {
                                guard enabled else { return }
                                for t in tools { enabledToolNames.remove(t.name) }
                            }
                            .disabled(!enabled)
                            .font(.caption)
                        }
                        
                        ForEach(tools) { tool in
                            Toggle(isOn: Binding(
                                get: { enabledToolNames.contains(tool.name) && enabled },
                                set: { on in
                                    guard enabled else { return }
                                    if on { enabledToolNames.insert(tool.name) } else { enabledToolNames.remove(tool.name) }
                                }
                            )) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(tool.name)
                                        .font(.subheadline)
                                    if !tool.description.isEmpty {
                                        Text(tool.description)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                            .disabled(!enabled)
                        }
                    }
                    .padding(.top, 8)
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: tag == "calendar" ? "calendar" : tag == "tasks" ? "checklist" : tag == "weather" ? "cloud.sun" : "envelope")
                        Text(title)
                            .font(.subheadline)
                            .fontWeight(.semibold)
                        Spacer()
                        Text("\(tools.count)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 10)
                    .padding(.horizontal, 12)
                    .background(Color(.systemGray6).opacity(0.6))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                .disabled(!enabled)
            }
        }
    }
}

// MARK: - Main Content View

struct ContentView: View {
    @EnvironmentObject var authManager: AuthManager
    @StateObject private var locationManager = LocationManager()

    private let chatClient = ChatClient()
    
    @State private var messages: [ChatMessage] = []
    @State private var draft = ""
    @State private var sessionId = UUID().uuidString
    @State private var isAwaitingResponse = false
    @State private var showTypingIndicator = false
    @State private var streamingText = ""  // Accumulates streaming text
    @State private var isUserAtBottom = true
    @State private var scrollViewHeight: CGFloat = 0
    @State private var scrollRequest = 0

    // Tool toggles + drawer
    @State private var isToolDrawerOpen = false
    @AppStorage("tools.enabled.calendar") private var calendarToolsEnabled = true
    @AppStorage("tools.enabled.gmail") private var gmailToolsEnabled = true
    @AppStorage("tools.enabled.tasks") private var tasksToolsEnabled = true
    @AppStorage("tools.enabled.weather") private var weatherToolsEnabled = true
    @AppStorage("user.timezone") private var timezoneName = "America/Chicago"
    @AppStorage("tools.enabled.names") private var enabledToolNamesStored = ""
    @AppStorage("voice.continuousMode") private var isContinuousMode = false
    @State private var availableTools: [ChatClient.ToolInfo] = []
    
    // Tool Success Toast
    struct ToolSuccessToast: Identifiable, Equatable {
        let id = UUID()
        let icon: String
        let title: String
        let subtitle: String
        var link: URL? = nil
        
        static func == (lhs: ToolSuccessToast, rhs: ToolSuccessToast) -> Bool {
            lhs.id == rhs.id
        }
    }
    @State private var toolSuccessToast: ToolSuccessToast?
    
    // Continuous Recording State
    @State private var audioEngine = AVAudioEngine()
    @State private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    @State private var recognitionTask: SFSpeechRecognitionTask?
    
    struct PendingEmailDraft: Identifiable, Equatable {
        let id: String // draftId
        let fromEmail: String?
        let to: String
        let cc: String?
        let bcc: String?
        let subject: String
        let body: String
    }
    
    @State private var pendingDraft: PendingEmailDraft?
    @State private var showDraftSheet = false
    @State private var isSendingDraft = false
    
    // Recording state
    @State private var isRecording = false
    @State private var recordingStart: Date?
    @State private var recordingElapsed: TimeInterval = 0
    @State private var waveformValues: [CGFloat] = Array(repeating: 0.2, count: 12)
    private var recordingTimer = Timer.publish(every: 0.05, on: .main, in: .common).autoconnect()
    
    // Audio playback
    @StateObject private var audioQueuePlayer = AudioQueuePlayer()
    @State private var audioPlayer: AVAudioPlayer?  // Legacy, kept for compatibility
    @State private var isAssistantSpeaking = false
    
    // Focus and keyboard
    @FocusState private var isComposerFocused: Bool
    @State private var isHistoryOpen = false
    @State private var isComposerExpanded = false
    
    private var assistantState: MeshOrbView.AssistantState {
        if isRecording { return .listening }
        if isAwaitingResponse { return .thinking }
        if isAssistantSpeaking { return .speaking }
        return .idle
    }
    
    private var orbAudioLevel: CGFloat {
        // Use the average of waveform values for the orb's overall reaction
        waveformValues.reduce(0, +) / CGFloat(waveformValues.count)
    }
    
    private var assistantColor: Color {
        switch assistantState {
        case .idle: return .blue
        case .listening: return .red
        case .thinking: return .purple
        case .speaking: return .cyan
        }
    }
    
    var body: some View {
        ZStack(alignment: .top) {
            // 1. Background layer
            Color.black.ignoresSafeArea()
                .zIndex(0)
            
            // Subtle animated background mesh (Cleaned up for simplicity)
            RadialGradient(
                colors: [assistantColor.opacity(0.1), .black],
                center: .center,
                startRadius: 0,
                endRadius: 600
            )
            .ignoresSafeArea()
            .contentShape(Rectangle())
            .onTapGesture {
                isComposerFocused = false
            }
            .zIndex(1)
            
            // 2. Main content layer (The Orb)
            VStack {
                Spacer()
                
                MeshOrbView(state: assistantState, audioLevel: orbAudioLevel)
                    .offset(y: showDraftSheet ? -100 : 0)
                    .onTapGesture {
                        if !isRecording && !isAwaitingResponse && !isAssistantSpeaking {
                            startRecording()
                        } else if isRecording {
                            sendMessage()
                        } else if isAssistantSpeaking {
                            stopAudio()
                        }
                    }
                
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .zIndex(2)
            
            // 3. UI Overlays (History, Composer, Tools)
            
            // Top Header (Profile & Timezone)
            VStack {
                HStack {
                    Spacer()
                    HStack(spacing: 16) {
                        // Timezone Picker
                        Menu {
                            ForEach(TimeZone.knownTimeZoneIdentifiers.prefix(20), id: \.self) { id in
                                Button {
                                    timezoneName = id
                                } label: {
                                    HStack {
                                        Text(id)
                                        if timezoneName == id {
                                            Image(systemName: "checkmark")
                                        }
                                    }
                                }
                            }
                            
                            Divider()
                            
                            Text("More in settings...")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        } label: {
                            Image(systemName: "globe")
                                .font(.system(size: 18))
                                .foregroundStyle(.white.opacity(0.6))
                                .padding(8)
                                .background(.white.opacity(0.01)) // Increase tap target
                        }
                        
                        // Profile/Account Button
                        Menu {
                            if let user = authManager.user {
                                Section {
                                    Text(user.email ?? "User")
                                        .font(.caption)
                                }
                                Button(role: .destructive) {
                                    authManager.signOut()
                                } label: {
                                    Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                                }
                            } else {
                                Button {
                                    Task {
                                        await authManager.signInWithGoogle()
                                    }
                                } label: {
                                    Label("Sign In with Google", systemImage: "person.crop.circle.badge.plus")
                                }
                            }
                        } label: {
                            Group {
                                if authManager.user != nil {
                                    Image(systemName: "person.crop.circle.fill")
                                        .font(.system(size: 24))
                                        .foregroundStyle(.white.opacity(0.8))
                                } else {
                                    Image(systemName: "person.crop.circle")
                                        .font(.system(size: 24))
                                        .foregroundStyle(.white.opacity(0.6))
                                }
                            }
                            .padding(8)
                            .background(.white.opacity(0.01)) // Increase tap target
                        }
                    }
                    .padding(.trailing, 20)
                    .padding(.top, 10)
                }
                Spacer()
            }
            .zIndex(10) // Below History Drawer
            
            // Pull handle hint
            VStack {
                Capsule()
                    .fill(.secondary.opacity(0.3))
                    .frame(width: 40, height: 5)
                    .padding(.top, 8)
                Spacer()
            }
            .contentShape(Rectangle())
            .frame(height: 100)
            .gesture(
                DragGesture()
                    .onEnded { value in
                        if value.translation.height > 50 {
                            withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                isHistoryOpen = true
                            }
                        }
                    }
            )
            .onTapGesture {
                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                    isHistoryOpen = true
                }
            }
            .zIndex(11)
            
            // Tool Success Toast (slides in below top handle)
            VStack {
                if let toast = toolSuccessToast {
                    HStack(spacing: 12) {
                        Image(systemName: toast.icon)
                            .font(.title2)
                            .foregroundStyle(.green)

                        VStack(alignment: .leading, spacing: 2) {
                            Text(toast.title)
                                .font(.subheadline)
                                .fontWeight(.semibold)
                            Text(toast.subtitle)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }

                        Spacer()

                        // Link button (if present)
                        if let url = toast.link {
                            Button {
                                UIApplication.shared.open(url)
                            } label: {
                                Image(systemName: "arrow.up.right.square")
                                    .font(.body)
                                    .foregroundStyle(.blue)
                            }
                        }
                        
                        // Close button
                        Button {
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) { 
                                toolSuccessToast = nil 
                            }
                        } label: {
                            Image(systemName: "xmark")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundStyle(.secondary)
                                .padding(6)
                                .background(Color.primary.opacity(0.1))
                                .clipShape(Circle())
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .shadow(color: .black.opacity(0.1), radius: 10, y: 5)
                    .padding(.horizontal, 16)
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
                Spacer()
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 60)
            .animation(.spring(response: 0.35, dampingFraction: 0.85), value: toolSuccessToast)
            .zIndex(5)
            
            // History Drawer
            HistoryDrawer(isOpen: $isHistoryOpen, messages: messages, showTypingIndicator: showTypingIndicator)
                .zIndex(20)
            
            // Drawer & Overlay
            if isToolDrawerOpen {
                Rectangle()
                    .fill(.ultraThinMaterial)
                    .ignoresSafeArea()
                    .onTapGesture {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.9)) {
                            isToolDrawerOpen = false
                        }
                    }
                    .zIndex(25)
            }
            
            ToolDrawerView(
                isOpen: $isToolDrawerOpen,
                calendarEnabled: $calendarToolsEnabled,
                gmailEnabled: $gmailToolsEnabled,
                tasksEnabled: $tasksToolsEnabled,
                weatherEnabled: $weatherToolsEnabled,
                timezoneName: $timezoneName,
                isContinuousMode: $isContinuousMode,
                availableTools: availableTools,
                enabledToolNames: Binding(
                    get: { enabledToolNames },
                    set: { enabledToolNames = $0; persistEnabledToolNames() }
                ),
                refreshTools: { Task { await refreshToolsList() } }
            )
            .zIndex(30)
            
            // Draft confirmation overlay
            if showDraftSheet, let d = pendingDraft {
                VStack {
                    Spacer()
                    
                    VStack(spacing: 16) {
                        // Header
                        HStack {
                            Text("Send Email?")
                                .font(.headline)
                            Spacer()
                            Button {
                                pendingDraft = nil
                                showDraftSheet = false
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.secondary)
                                    .font(.title2)
                            }
                        }
                        
                        // Compact draft info
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("To:")
                                    .foregroundStyle(.secondary)
                                Text(d.to)
                                    .fontWeight(.medium)
                            }
                            .font(.subheadline)
                            
                            Text(d.subject)
                                .font(.subheadline)
                                .fontWeight(.semibold)
                            
                            Text(d.body)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(3)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        
                        // Voice recording UI
                        if isRecording {
                            VStack(spacing: 12) {
                                RecordingWaveformView(levels: waveformValues, color: .blue.opacity(0.7), scale: 35)
                                    .frame(height: 40)
                                
                                if !draft.isEmpty {
                                    Text(draft)
                                        .font(.subheadline)
                                        .foregroundStyle(.primary)
                                        .multilineTextAlignment(.center)
                                        .lineLimit(2)
                                } else {
                                    Text("Say \"Send\" or \"Cancel\"")
                                        .font(.caption)
                                        .foregroundStyle(.blue)
                                }
                            }
                            .padding(.vertical, 8)
                        } else if isAssistantSpeaking {
                            Button {
                                // Tap to interrupt assistant and speak
                                stopAudio()
                            } label: {
                                VStack(spacing: 8) {
                                    HStack(spacing: 8) {
                                        ProgressView()
                                        Text("Assistant speaking...")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Text("Tap to interrupt")
                                        .font(.caption2)
                                        .foregroundStyle(.blue)
                                }
                            }
                            .buttonStyle(.plain)
                            .padding(.vertical, 8)
                        }
                        
                        // Actions
                        HStack(spacing: 12) {
                            Button(role: .destructive) {
                                pendingDraft = nil
                                showDraftSheet = false
                            } label: {
                                Text("Cancel")
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 12)
                                    .background(Color(.systemGray5))
                                    .clipShape(RoundedRectangle(cornerRadius: 12))
                            }
                            
                            Button {
                                Task {
                                    await sendDraft(d)
                                }
                            } label: {
                                if isSendingDraft {
                                    ProgressView()
                                        .tint(.white)
                                } else {
                                    Text("Send Email")
                                        .fontWeight(.semibold)
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Color.blue)
                            .foregroundStyle(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                            .disabled(isSendingDraft)
                        }
                    }
                    .padding(20)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
                    .shadow(color: .black.opacity(0.15), radius: 20, y: -5)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 8)
                }
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .animation(.spring(response: 0.35, dampingFraction: 0.85), value: showDraftSheet)
                .zIndex(35)
            }
            
            // Bottom UI (Drawer trigger & Composer)
            VStack {
                Spacer()
                HStack(spacing: 20) {
                    // Tool Drawer Trigger
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.9)) {
                            isToolDrawerOpen.toggle()
                        }
                    } label: {
                        Image(systemName: "line.3.horizontal")
                            .font(.title3)
                            .foregroundStyle(.white.opacity(0.6))
                            .padding(12)
                            .background(.white.opacity(0.1))
                            .clipShape(Circle())
                    }
                    
                    Spacer()
                    
                    // Floating Composer
                    HStack {
                        if isComposerExpanded {
                            TextField("Type message...", text: $draft)
                                .focused($isComposerFocused)
                                .onSubmit { sendMessage() }
                                .padding(.horizontal, 12)
                                .frame(height: 44)
                            
                            Button {
                                sendMessage()
                            } label: {
                                Image(systemName: "arrow.up.circle.fill")
                                    .font(.title2)
                                    .foregroundStyle(.blue)
                            }
                            .padding(.trailing, 8)
                        } else {
                            Button {
                                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                    isComposerExpanded = true
                                    isComposerFocused = true
                                }
                            } label: {
                                Image(systemName: "keyboard")
                                    .font(.title3)
                                    .foregroundStyle(.white.opacity(0.6))
                                    .padding(12)
                            }
                        }
                    }
                    .background(.white.opacity(0.1))
                    .clipShape(Capsule())
                    .frame(width: isComposerExpanded ? nil : 44)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 20)
            }
            .zIndex(40)
        }
        .onChange(of: isComposerFocused) { _, focused in
            if !focused {
                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                    isComposerExpanded = false
                }
            }
        }
        .toolbar(.hidden)
        .onReceive(audioQueuePlayer.$isSpeaking) { speaking in
            isAssistantSpeaking = speaking
            if speaking {
                let barCount = waveformValues.count
                waveformValues = Array(repeating: 0.1, count: barCount)
            }
        }
        .onChange(of: isAwaitingResponse) { _, awaiting in
            if awaiting {
                // Reset waveform to flat baseline while waiting for assistant
                let barCount = waveformValues.count
                waveformValues = Array(repeating: 0.1, count: barCount)
            }
        }
        .onChange(of: showDraftSheet) { _, showing in
            // Auto-start recording when draft popup appears (wait for audio to finish)
            if showing && !isRecording && !isAssistantSpeaking {
                startRecording()
            }
            // Stop recording when popup dismissed
            if !showing && isRecording {
                stopContinuousRecording()
            }
        }
        .onChange(of: isAssistantSpeaking) { _, speaking in
            // Start recording after assistant stops (if draft popup is showing)
            if !speaking && showDraftSheet && !isRecording {
                startRecording()
            }
        }
        .task {
            // Request location permission for weather
            locationManager.requestPermission()
            
            await refreshToolsList()
            // If this is the first launch (no stored selection yet), default-enable all tools we know about.
            if enabledToolNamesStored.isEmpty, !availableTools.isEmpty {
                enabledToolNames = Set(availableTools.map { $0.name })
                persistEnabledToolNames()
            } else {
                enabledToolNames = loadEnabledToolNames()
            }
            // Ensure service toggles act as master switches for their tools
            applyServiceTogglesToToolSelection()
        }
        .onChange(of: calendarToolsEnabled) { _, _ in
            applyServiceTogglesToToolSelection()
        }
        .onChange(of: gmailToolsEnabled) { _, _ in
            applyServiceTogglesToToolSelection()
        }
        .onChange(of: tasksToolsEnabled) { _, _ in
            applyServiceTogglesToToolSelection()
        }
        .onChange(of: weatherToolsEnabled) { _, _ in
            applyServiceTogglesToToolSelection()
        }
    }

    private var allowedToolTags: [String]? {
        var tags: [String] = []
        if calendarToolsEnabled { tags.append("calendar") }
        if gmailToolsEnabled { tags.append("gmail") }
        if tasksToolsEnabled { tags.append("tasks") }
        if weatherToolsEnabled { tags.append("weather") }
        return tags
    }

    @State private var enabledToolNames: Set<String> = []
    
    private var allowedToolNames: [String]? {
        // If the user hasn't loaded tools yet, don't constrain by name (use tags only).
        guard !availableTools.isEmpty else { return nil }
        // Explicit empty array means "no tools allowed" (server respects this).
        return Array(enabledToolNames).sorted()
    }
    
    private func formatTime(_ interval: TimeInterval) -> String {
        let minutes = Int(interval) / 60
        let seconds = Int(interval) % 60
        return String(format: "%d:%02d", minutes, seconds)
    }
    
    // MARK: - Send Message
    
    private func sendMessage() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        
        // Stop any currently playing audio when starting new message
        stopAudio()
        
        let userMessage = ChatMessage(text: text, isUser: true)
        messages.append(userMessage)
        
        if isContinuousMode && isRecording {
            // In continuous mode, we just clear the draft and restart the recognition task
            // so the next spoken words start from an empty string.
            draft = ""
            restartContinuousRecognition()
        } else {
            // If NOT in continuous mode but recording, stop the session
            if isRecording {
                stopContinuousRecording()
            }
            draft = ""
        }
        
        isComposerFocused = false
        isAwaitingResponse = true
        showTypingIndicator = true
        streamingText = ""
        
        Task {
            do {
                // Refresh token if needed before sending
                let token = await authManager.refreshGoogleToken()
                
                let stream = chatClient.sendMessageStreaming(
                    sessionId: sessionId,
                    message: text,
                    userId: authManager.user?.uid,
                    googleAccessToken: token,
                    allowedToolNames: allowedToolNames,
                    allowedToolTags: allowedToolTags,
                    timezoneName: timezoneName,
                    userLatitude: locationManager.location?.coordinate.latitude,
                    userLongitude: locationManager.location?.coordinate.longitude
                )
                
                for try await event in stream {
                    switch event {
                    case .textDelta(let chunk):
                        // Append to streaming text and update the last message
                        if showTypingIndicator, !chunk.isEmpty {
                            // First text arrived: hide typing indicator
                            showTypingIndicator = false
                        }
                        streamingText = appendStreamingText(existing: streamingText, chunk: chunk)
                        // Only create the assistant bubble once we actually have text
                        if let last = messages.last, last.isUser {
                            if !streamingText.isEmpty {
                                messages.append(ChatMessage(text: streamingText, isUser: false))
                            }
                        } else if let lastIndex = messages.indices.last, !messages[lastIndex].isUser {
                            messages[lastIndex] = ChatMessage(text: streamingText, isUser: false)
                        }
                        if isUserAtBottom {
                            scrollRequest += 1
                        }
                        
                    case .toolCall(let name, let arguments):
                        print("[Stream] Tool call: \(name)")
                        print("[Stream] Tool args: \(arguments)")
                        
                    case .toolResult(let name, let result):
                        let snippet = String(result.prefix(500))
                        print("[Stream] Tool result: \(name)")
                        print("[Stream] Tool result (first 500 chars): \(snippet)")
                        
                        // If we created an email draft, show a UI confirmation sheet.
                        if name == "create_gmail_draft" {
                            if let draft = parseDraftFromToolResult(result) {
                                pendingDraft = draft
                                showDraftSheet = true
                            }
                        }
                        
                        // Show success toast for other tool results
                        showToolSuccessToast(name: name, result: result)
                        
                    case .audio(let base64, _):
                        playAudio(base64: base64)
                        
                    case .done(let fullText):
                        isAwaitingResponse = false
                        showTypingIndicator = false
                        // Ensure final text is set
                        let finalText = fullText.isEmpty ? streamingText : fullText
                        if let lastIndex = messages.indices.last, !messages[lastIndex].isUser {
                            messages[lastIndex] = ChatMessage(text: finalText, isUser: false)
                        } else if !finalText.isEmpty {
                            messages.append(ChatMessage(text: finalText, isUser: false))
                        }
                        streamingText = ""
                        if isUserAtBottom {
                            scrollRequest += 1
                        }
                        
                    case .error(let message):
                        isAwaitingResponse = false
                        showTypingIndicator = false
                        let errText = "Error: \(message)"
                        if let lastIndex = messages.indices.last, !messages[lastIndex].isUser {
                            messages[lastIndex] = ChatMessage(text: errText, isUser: false)
                        } else {
                            messages.append(ChatMessage(text: errText, isUser: false))
                        }
                        streamingText = ""
                        if isUserAtBottom {
                            scrollRequest += 1
                        }
                    }
                }
            } catch {
                isAwaitingResponse = false
                showTypingIndicator = false
                if let lastIndex = messages.indices.last, !messages[lastIndex].isUser {
                    messages[lastIndex] = ChatMessage(text: "Error: \(error.localizedDescription)", isUser: false)
                } else {
                    messages.append(ChatMessage(text: "Error: \(error.localizedDescription)", isUser: false))
                }
                streamingText = ""
            }
        }
    }

    @MainActor
    private func sendDraft(_ d: PendingEmailDraft) async {
        isSendingDraft = true
        do {
            let token = await authManager.refreshGoogleToken()
            _ = try await chatClient.sendGmailDraft(
                draftId: d.id,
                userId: authManager.user?.uid,
                googleAccessToken: token
            )
            isSendingDraft = false
            showDraftSheet = false
            pendingDraft = nil
            messages.append(ChatMessage(text: "Sent email to \(d.to) — “\(d.subject)”.", isUser: false))
        } catch {
            isSendingDraft = false
            messages.append(ChatMessage(text: "Failed to send email: \(error.localizedDescription)", isUser: false))
        }
    }
    
    private func parseDraftFromToolResult(_ result: String) -> PendingEmailDraft? {
        guard let data = result.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        guard (json["status"] as? String) == "success" else { return nil }
        guard let draftId = json["draftId"] as? String else { return nil }
        let to = (json["to"] as? String) ?? ""
        let subject = (json["subject"] as? String) ?? ""
        let body = (json["body"] as? String) ?? ""
        if to.isEmpty || subject.isEmpty {
            return nil
        }
        let fromEmail = json["fromEmail"] as? String
        let cc = json["cc"] as? String
        let bcc = json["bcc"] as? String
        return PendingEmailDraft(
            id: draftId,
            fromEmail: fromEmail,
            to: to,
            cc: cc,
            bcc: bcc,
            subject: subject,
            body: body
        )
    }
    
    private func showToolSuccessToast(name: String, result: String) {
        guard let data = result.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              (json["status"] as? String) == "success" else {
            return
        }
        
        var toast: ToolSuccessToast?
        
        switch name {
        case "create_google_calendar_event":
            if let event = json["event"] as? [String: Any] {
                let summary = event["summary"] as? String ?? "Event"
                let location = event["location"] as? String
                
                // Parse start time for display
                var timeDisplay = ""
                if let start = event["start"] as? [String: Any] {
                    if let dateTime = start["dateTime"] as? String {
                        let parts = dateTime.components(separatedBy: "T")
                        if parts.count > 1 {
                            let timePart = parts[1].prefix(5) // "17:00"
                            timeDisplay = String(timePart)
                        }
                    }
                }
                
                var subtitle = summary
                if !timeDisplay.isEmpty {
                    subtitle += " at \(timeDisplay)"
                }
                if let loc = location, !loc.isEmpty {
                    subtitle += " • \(loc)"
                }
                
                // No link for now - Google's event links are unreliable on iOS
                toast = ToolSuccessToast(
                    icon: "calendar.badge.checkmark",
                    title: "Event Created",
                    subtitle: subtitle,
                    link: nil
                )
            }
            
        case "create_task":
            let title = json["title"] as? String ?? "Task"
            let due = json["due"] as? String
            let dueDisplay = due?.prefix(10) ?? ""
            toast = ToolSuccessToast(
                icon: "checkmark.circle.fill",
                title: "Task Added",
                subtitle: title + (dueDisplay.isEmpty ? "" : " • Due \(dueDisplay)")
            )
            
        case "complete_task":
            let message = json["message"] as? String ?? "Task completed"
            toast = ToolSuccessToast(
                icon: "checkmark.circle.fill",
                title: "Task Completed",
                subtitle: message
            )
            
        case "get_weather":
            if let location = json["location"] as? String {
                if let current = json["current"] as? [String: Any] {
                    let temp = current["temperature_f"] as? Int ?? 0
                    let condition = current["condition"] as? String ?? ""
                    toast = ToolSuccessToast(
                        icon: "cloud.sun.fill",
                        title: "\(temp)°F - \(condition)",
                        subtitle: location
                    )
                } else if let forecast = json["forecast"] as? [[String: Any]], let first = forecast.first {
                    // This handles the "specific date" case
                    let high = first["high_f"] as? Int ?? 0
                    let low = first["low_f"] as? Int ?? 0
                    let condition = first["condition"] as? String ?? ""
                    let date = first["date"] as? String ?? ""
                    toast = ToolSuccessToast(
                        icon: "calendar.day.timeline.left",
                        title: "\(high)°F / \(low)°F",
                        subtitle: "\(condition) in \(location) on \(date)"
                    )
                }
            }
            
        case "send_gmail_draft":
            toast = ToolSuccessToast(
                icon: "paperplane.fill",
                title: "Email Sent",
                subtitle: "Your email has been sent successfully"
            )
            
        case "mark_gmail_emails_read":
            let count = (json["updated"] as? [String])?.count ?? 0
            toast = ToolSuccessToast(
                icon: "envelope.open.fill",
                title: "Marked as Read",
                subtitle: "\(count) email(s) marked as read"
            )
            
        default:
            break
        }
        
        if let toast = toast {
            withAnimation {
                toolSuccessToast = toast
            }
            // Auto-dismiss after 4 seconds
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
                withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) {
                    if self.toolSuccessToast?.id == toast.id {
                        self.toolSuccessToast = nil
                    }
                }
            }
        }
    }

    /// Appends streaming text chunks while fixing common missing-space boundaries like "hours.There".
    private func appendStreamingText(existing: String, chunk: String) -> String {
        guard !chunk.isEmpty else { return existing }
        guard !existing.isEmpty else { return chunk }
        
        // If the model streams "Sentence." then next chunk "Next sentence" without a leading space,
        // insert a space after punctuation.
        let lastChar = existing.last!
        let firstChar = chunk.first!
        
        let lastIsSentencePunct = (lastChar == "." || lastChar == "!" || lastChar == "?")
        let chunkStartsWithWhitespace = firstChar.isWhitespace || firstChar == "\n"
        let chunkStartsWithLetterOrDigit = firstChar.isLetter || firstChar.isNumber
        
        if lastIsSentencePunct && !chunkStartsWithWhitespace && chunkStartsWithLetterOrDigit {
            // Avoid splitting email addresses/domains like "egor@gmail." + "com"
            if lastChar == "." {
                let token = existing.split(whereSeparator: { $0.isWhitespace }).last.map(String.init) ?? ""
                if token.contains("@") {
                    return existing + chunk
                }
            }
            return existing + " " + chunk
        }
        
        return existing + chunk
    }

    // MARK: - Tools selection persistence + loading
    
    private func persistEnabledToolNames() {
        let arr = Array(enabledToolNames).sorted()
        enabledToolNamesStored = (try? String(data: JSONEncoder().encode(arr), encoding: .utf8)) ?? ""
    }
    
    private func loadEnabledToolNames() -> Set<String> {
        guard let data = enabledToolNamesStored.data(using: .utf8),
              let arr = try? JSONDecoder().decode([String].self, from: data) else {
            return []
        }
        return Set(arr)
    }
    
    private func applyServiceTogglesToToolSelection() {
        // Remove tools for any disabled service (master kill-switch behavior)
        let calendarTools = availableTools.filter { $0.tags.contains("calendar") }.map { $0.name }
        let gmailTools = availableTools.filter { $0.tags.contains("gmail") }.map { $0.name }
        let tasksTools = availableTools.filter { $0.tags.contains("tasks") }.map { $0.name }
        let weatherTools = availableTools.filter { $0.tags.contains("weather") }.map { $0.name }
        
        if !calendarToolsEnabled {
            for name in calendarTools { enabledToolNames.remove(name) }
        } else if calendarToolsEnabled && !calendarTools.isEmpty {
            // If calendar is enabled but none of its tools are selected, default-enable them.
            let selectedCalendarTools = enabledToolNames.intersection(Set(calendarTools))
            if selectedCalendarTools.isEmpty {
                for name in calendarTools { enabledToolNames.insert(name) }
            }
        }
        
        if !gmailToolsEnabled {
            for name in gmailTools { enabledToolNames.remove(name) }
        } else if gmailToolsEnabled && !gmailTools.isEmpty {
            let selectedGmailTools = enabledToolNames.intersection(Set(gmailTools))
            if selectedGmailTools.isEmpty {
                for name in gmailTools { enabledToolNames.insert(name) }
            }
        }
        
        if !tasksToolsEnabled {
            for name in tasksTools { enabledToolNames.remove(name) }
        } else if tasksToolsEnabled && !tasksTools.isEmpty {
            let selectedTasksTools = enabledToolNames.intersection(Set(tasksTools))
            if selectedTasksTools.isEmpty {
                for name in tasksTools { enabledToolNames.insert(name) }
            }
        }
        
        if !weatherToolsEnabled {
            for name in weatherTools { enabledToolNames.remove(name) }
        } else if weatherToolsEnabled && !weatherTools.isEmpty {
            let selectedWeatherTools = enabledToolNames.intersection(Set(weatherTools))
            if selectedWeatherTools.isEmpty {
                for name in weatherTools { enabledToolNames.insert(name) }
            }
        }
        
        persistEnabledToolNames()
    }
    
    @MainActor
    private func refreshToolsList() async {
        do {
            let tools = try await chatClient.fetchTools()
            availableTools = tools
        } catch {
            print("[Tools] Failed to fetch tools: \(error)")
        }
    }
    
    // MARK: - Recording
    
    private func startRecording() {
        stopAudio()
        
        SFSpeechRecognizer.requestAuthorization { status in
            guard status == .authorized else {
                print("[Recording] Not authorized: \(status)")
                return
            }
            
            DispatchQueue.main.async {
                self.beginContinuousSession()
            }
        }
    }
    
    // MARK: - Continuous Recording (Streaming)
    
    private func beginContinuousSession() {
        do {
            // Cancel any existing task
            recognitionTask?.cancel()
            recognitionTask = nil
            
            let session = AVAudioSession.sharedInstance()
            // Use .spokenAudio mode here as well to ensure consistent gain profiles
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetoothA2DP])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            
            recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
            guard let recognitionRequest = recognitionRequest else { return }
            recognitionRequest.shouldReportPartialResults = true
            recognitionRequest.taskHint = .dictation  // Hint for longer sessions
            
            let inputNode = audioEngine.inputNode
            let recordingFormat = inputNode.outputFormat(forBus: 0)
            
            inputNode.removeTap(onBus: 0)
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { buffer, _ in
                self.recognitionRequest?.append(buffer)
                self.updateMeterFromBuffer(buffer)
            }
            
            audioEngine.prepare()
            try audioEngine.start()
            
            recognitionTask = SFSpeechRecognizer()?.recognitionTask(with: recognitionRequest) { result, error in
                if let result = result {
                    DispatchQueue.main.async {
                        self.draft = result.bestTranscription.formattedString
                    }
                }
                
                if error != nil || result?.isFinal == true {
                    // Recognition ended - just stop, don't auto-restart
                    DispatchQueue.main.async {
                        self.stopContinuousRecording()
                    }
                }
            }
            
            self.isRecording = true
            self.recordingStart = Date()
            self.recordingElapsed = 0
            self.waveformValues = Array(repeating: 0.1, count: 12)
            
        } catch {
            print("[Continuous] Failed to start: \(error)")
        }
    }
    
    private func stopContinuousRecording() {
        isRecording = false
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
        recordingStart = nil
        recordingElapsed = 0
        waveformValues = Array(repeating: 0.2, count: 12)
    }
    
    private func restartContinuousRecognition() {
        // We don't stop the audioEngine, just refresh the recognition task
        recognitionTask?.cancel()
        recognitionTask = nil
        
        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let recognitionRequest = recognitionRequest else { return }
        recognitionRequest.shouldReportPartialResults = true
        
        recognitionTask = SFSpeechRecognizer()?.recognitionTask(with: recognitionRequest) { result, error in
            if let result = result {
                DispatchQueue.main.async {
                    self.draft = result.bestTranscription.formattedString
                }
            }
            if error != nil || result?.isFinal == true {
                // If it fails, we'll need a full restart from the loop in beginContinuousSession
                // but usually cancel() is what we want here.
            }
        }
    }
    
    private func pauseRecognition() {
        // Stop feeding audio to speech recognizer (prevents transcribing bot audio)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
    }
    
    private func resumeRecognition() {
        // Restart recognition after pause (reuses existing audio engine)
        guard audioEngine.isRunning else { return }
        
        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let recognitionRequest = recognitionRequest else { return }
        recognitionRequest.shouldReportPartialResults = true
        
        recognitionTask = SFSpeechRecognizer()?.recognitionTask(with: recognitionRequest) { result, error in
            if let result = result {
                DispatchQueue.main.async {
                    self.draft = result.bestTranscription.formattedString
                }
            }
        }
    }
    
    private func updateMeterFromBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let channelData = buffer.floatChannelData?[0] else { return }
        let channelDataArray = stride(from: 0, to: Int(buffer.frameLength), by: buffer.stride).map { channelData[$0] }
        
        let rms = sqrt(channelDataArray.map { $0 * $0 }.reduce(0, +) / Float(buffer.frameLength))
        // Damped scaling: rms is usually 0.0 - 0.1 for normal speech.
        // rms * 4.0 keeps it in a nice 0.1 - 0.5 range mostly.
        let level = CGFloat(max(0.05, min(0.8, rms * 4.0)))
        
        DispatchQueue.main.async {
            // Freeze waveform while waiting for response or during playback
            guard !self.isAssistantSpeaking && !self.isAwaitingResponse else { return }
            self.waveformValues.removeFirst()
            self.waveformValues.append(level)
        }
    }
    
    private func updateMeter() {
        // No-op: handled by updateMeterFromBuffer in the Engine path
    }
    
    // MARK: - Audio Playback
    
    private func stopAudio() {
        audioQueuePlayer.stopAll()
    }
    
    private func playAudio(base64: String) {
        guard let audioData = Data(base64Encoded: base64) else {
            print("[Audio] Failed to decode base64")
            return
        }
        
        // Enqueue audio for sequential playback
        audioQueuePlayer.enqueue(audioData: audioData)
    }
}

#Preview {
    ContentView()
        .environmentObject(AuthManager())
}
