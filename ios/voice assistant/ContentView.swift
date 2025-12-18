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

// MARK: - Data Models

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    let text: String
    let isUser: Bool
    let timestamp = Date()
}

// MARK: - Chat Client

class ChatClient {
    private let baseURL = "http://192.168.1.153:5050"
    
    struct ChatRequest: Encodable {
        let session_id: String
        let message: String
        let user_id: String?
        let google_access_token: String?
        let allowed_tool_tags: [String]?
        let timezone_name: String?
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
        allowedToolTags: [String]?,
        timezoneName: String?
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
            allowed_tool_tags: allowedToolTags,
            timezone_name: timezoneName
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
        allowedToolTags: [String]?,
        timezoneName: String?
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
                        allowed_tool_tags: allowedToolTags,
                        timezone_name: timezoneName
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
    
    func enqueue(audioData: Data) {
        audioQueue.append(audioData)
        playNextIfNeeded()
    }
    
    func stopAll() {
        audioQueue.removeAll()
        currentPlayer?.stop()
        currentPlayer = nil
        isPlaying = false
        print("[AudioQueue] Stopped and cleared queue")
    }
    
    private func playNextIfNeeded() {
        guard !isPlaying, !audioQueue.isEmpty else { return }
        
        let audioData = audioQueue.removeFirst()
        
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)
            
            currentPlayer = try AVAudioPlayer(data: audioData)
            currentPlayer?.delegate = self
            currentPlayer?.prepareToPlay()
            currentPlayer?.play()
            isPlaying = true
            print("[AudioQueue] Playing chunk, \(audioQueue.count) remaining in queue")
        } catch {
            print("[AudioQueue] Playback error: \(error)")
            isPlaying = false
            playNextIfNeeded()  // Try next chunk
        }
    }
    
    // AVAudioPlayerDelegate
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        isPlaying = false
        print("[AudioQueue] Chunk finished, success: \(flag)")
        playNextIfNeeded()
    }
    
    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        print("[AudioQueue] Decode error: \(error?.localizedDescription ?? "unknown")")
        isPlaying = false
        playNextIfNeeded()
    }
}

// MARK: - Waveform View

struct RecordingWaveformView: View {
    let levels: [CGFloat]
    
    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<levels.count, id: \.self) { index in
                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.red)
                    .frame(width: 4, height: max(4, levels[index] * 30))
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
    @Binding var timezoneName: String
    
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
                .background(Color(.systemBackground))
                .shadow(radius: 8)
                .offset(x: isOpen ? 0 : -340)
                .animation(.spring(response: 0.3, dampingFraction: 0.9), value: isOpen)
                
                Spacer()
            }
            .ignoresSafeArea()
            .allowsHitTesting(isOpen)
        }
    }
}

// MARK: - Main Content View

struct ContentView: View {
    @EnvironmentObject var authManager: AuthManager
    
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
    @AppStorage("user.timezone") private var timezoneName = "America/Chicago"
    
    // Recording state
    @State private var isRecording = false
    @State private var recordingStart: Date?
    @State private var recordingElapsed: TimeInterval = 0
    @State private var recordingURL: URL?
    @State private var audioRecorder: AVAudioRecorder?
    @State private var waveformValues: [CGFloat] = Array(repeating: 0.2, count: 12)
    private var recordingTimer = Timer.publish(every: 0.05, on: .main, in: .common).autoconnect()
    
    // Audio playback
    @StateObject private var audioQueuePlayer = AudioQueuePlayer()
    @State private var audioPlayer: AVAudioPlayer?  // Legacy, kept for compatibility
    
    // Focus and keyboard
    @FocusState private var isComposerFocused: Bool
    
    var body: some View {
        ZStack(alignment: .leading) {
            NavigationStack {
                VStack(spacing: 0) {
                    // Messages
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(spacing: 8) {
                                ForEach(messages) { message in
                                    chatBubble(for: message)
                                        .id(message.id)
                                }
                                
                                if showTypingIndicator {
                                    HStack {
                                        TypingIndicatorView()
                                        Spacer()
                                    }
                                    .padding(.horizontal, 16)
                                    .id("typing")
                                }

                                // Bottom marker (used to detect if user is scrolled to bottom)
                                Color.clear
                                    .frame(height: 1)
                                    .id("bottom")
                                    .background(
                                        GeometryReader { geo in
                                            Color.clear.preference(
                                                key: BottomMarkerYKey.self,
                                                value: geo.frame(in: .named("chatScroll")).maxY
                                            )
                                        }
                                    )
                            }
                            .padding(.vertical, 12)
                        }
                        .coordinateSpace(name: "chatScroll")
                        .background(
                            GeometryReader { geo in
                                Color.clear.preference(key: ScrollViewHeightKey.self, value: geo.size.height)
                            }
                        )
                        .onChange(of: messages.count) { _, _ in
                            if isUserAtBottom {
                                scrollToBottom(proxy: proxy)
                            }
                        }
                        .onChange(of: isAwaitingResponse) { _, _ in
                            if isUserAtBottom {
                                scrollToBottom(proxy: proxy)
                            }
                        }
                        .onChange(of: showTypingIndicator) { _, _ in
                            if isUserAtBottom {
                                scrollToBottom(proxy: proxy)
                            }
                        }
                        .onChange(of: scrollRequest) { _, _ in
                            if isUserAtBottom {
                                scrollToBottom(proxy: proxy, delay: 0)
                            }
                        }
                        .onPreferenceChange(ScrollViewHeightKey.self) { height in
                            scrollViewHeight = height
                        }
                        .onPreferenceChange(BottomMarkerYKey.self) { bottomY in
                            // If the bottom marker is within the visible scroll view (plus a small threshold),
                            // treat the user as "at bottom".
                            let threshold: CGFloat = 40
                            isUserAtBottom = bottomY <= (scrollViewHeight + threshold)
                        }
                        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillShowNotification)) { _ in
                            scrollToBottom(proxy: proxy, delay: 0.1)
                        }
                    }
                    
                    Divider()
                    
                    // Composer
                    composer
                }
                .navigationTitle("Assistant")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .navigationBarLeading) {
                        HStack(spacing: 10) {
                            Button {
                                withAnimation(.spring(response: 0.3, dampingFraction: 0.9)) {
                                    isToolDrawerOpen.toggle()
                                }
                            } label: {
                                Image(systemName: "line.3.horizontal")
                                    .foregroundStyle(.blue)
                            }
                            
                            Menu {
                                Button("Chicago (CT)") { timezoneName = "America/Chicago" }
                                Button("New York (ET)") { timezoneName = "America/New_York" }
                                Button("Los Angeles (PT)") { timezoneName = "America/Los_Angeles" }
                                Button("UTC") { timezoneName = "UTC" }
                            } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: "globe")
                                    Text(timezoneName == "America/Chicago" ? "CT" :
                                         timezoneName == "America/New_York" ? "ET" :
                                         timezoneName == "America/Los_Angeles" ? "PT" : "UTC")
                                        .font(.caption)
                                        .fontWeight(.semibold)
                                }
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Color(.systemGray6))
                                .clipShape(Capsule())
                            }
                        }
                    }
                    
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Menu {
                            if let email = authManager.user?.email {
                                Text(email)
                            }
                            Button(role: .destructive) {
                                authManager.signOut()
                            } label: {
                                Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                            }
                        } label: {
                            Image(systemName: "person.circle.fill")
                                .foregroundStyle(.blue)
                        }
                    }
                }
                .onReceive(recordingTimer) { _ in
                    guard isRecording, let start = recordingStart else { return }
                    recordingElapsed = Date().timeIntervalSince(start)
                    updateMeter()
                }
            }
            .disabled(isToolDrawerOpen)
            
            if isToolDrawerOpen {
                Color.black.opacity(0.35)
                    .ignoresSafeArea()
                    .onTapGesture {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.9)) {
                            isToolDrawerOpen = false
                        }
                    }
            }
            
            ToolDrawerView(
                isOpen: $isToolDrawerOpen,
                calendarEnabled: $calendarToolsEnabled,
                gmailEnabled: $gmailToolsEnabled,
                timezoneName: $timezoneName
            )
        }
    }

    private var allowedToolTags: [String]? {
        var tags: [String] = []
        if calendarToolsEnabled { tags.append("calendar") }
        if gmailToolsEnabled { tags.append("gmail") }
        return tags
    }
    
    // MARK: - Composer
    
    private var composer: some View {
        HStack(spacing: 8) {
            if isRecording {
                // Recording UI
                RecordingWaveformView(levels: waveformValues)
                    .frame(height: 30)
                    .frame(maxWidth: .infinity, alignment: .leading)
                
                Text(formatTime(recordingElapsed))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            } else {
                // Text field
                TextField("Message", text: $draft)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(Color(.systemGray6))
                    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .focused($isComposerFocused)
            }
            
            // Mic button
            Button {
                toggleRecording()
            } label: {
                Image(systemName: isRecording ? "stop.circle.fill" : "mic.fill")
                    .font(.title2)
                    .foregroundStyle(isRecording ? Color.red : Color.blue)
            }
            
            // Send button
            if !isRecording {
                Button {
                    sendMessage()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                        .foregroundStyle(draft.isEmpty ? Color.gray : Color.blue)
                }
                .disabled(draft.isEmpty || isAwaitingResponse)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
    
    // MARK: - Chat Bubble
    
    private func chatBubble(for message: ChatMessage) -> some View {
        HStack {
            if message.isUser { Spacer(minLength: 60) }
            
            Group {
                if message.isUser {
                    Text(message.text)
                } else {
                    // Render assistant messages with markdown formatting for readability
                    Text(.init(message.text))
                }
            }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(message.isUser ? Color.blue : Color(.systemGray5))
                .foregroundStyle(message.isUser ? Color.white : Color.primary)
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            
            if !message.isUser { Spacer(minLength: 60) }
        }
        .padding(.horizontal, 16)
    }
    
    // MARK: - Helpers
    
    private func scrollToBottom(proxy: ScrollViewProxy, delay: Double = 0.05) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            withAnimation(.easeOut(duration: 0.2)) {
                if showTypingIndicator {
                    proxy.scrollTo("typing", anchor: .bottom)
                } else if let lastMessage = messages.last {
                    proxy.scrollTo(lastMessage.id, anchor: .bottom)
                }
            }
        }
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
        draft = ""
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
                    allowedToolTags: allowedToolTags,
                    timezoneName: timezoneName
                )
                
                for try await event in stream {
                    await MainActor.run {
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
                }
            } catch {
                await MainActor.run {
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
            return existing + " " + chunk
        }
        
        return existing + chunk
    }
    
    // MARK: - Recording
    
    private func toggleRecording() {
        if isRecording {
            stopRecording()
        } else {
            startRecording()
        }
    }
    
    private func startRecording() {
        // Stop any playing audio first
        stopAudio()
        
        // Request permission
        if #available(iOS 17.0, *) {
            AVAudioApplication.requestRecordPermission { granted in
                guard granted else { return }
                self.beginRecordingSession()
            }
        } else {
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                guard granted else { return }
                self.beginRecordingSession()
            }
        }
    }
    
    private func beginRecordingSession() {
        DispatchQueue.main.async {
            do {
                let session = AVAudioSession.sharedInstance()
                try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothA2DP])
                try session.setActive(true)
                
                let url = FileManager.default.temporaryDirectory
                    .appendingPathComponent("voice-\(UUID().uuidString).m4a")
                
                let settings: [String: Any] = [
                    AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                    AVSampleRateKey: 44100,
                    AVNumberOfChannelsKey: 1,
                    AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
                ]
                
                let recorder = try AVAudioRecorder(url: url, settings: settings)
                recorder.isMeteringEnabled = true
                recorder.record()
                
                self.audioRecorder = recorder
                self.recordingURL = url
                self.recordingStart = Date()
                self.recordingElapsed = 0
                self.isRecording = true
                self.waveformValues = Array(repeating: 0.2, count: 12)
                
            } catch {
                print("[Recording] Failed to start: \(error)")
            }
        }
    }
    
    private func stopRecording() {
        let duration = recordingElapsed
        let url = recordingURL
        
        audioRecorder?.stop()
        audioRecorder = nil
        recordingStart = nil
        recordingElapsed = 0
        isRecording = false
        waveformValues = Array(repeating: 0.2, count: 12)
        
        print("[Recording] Duration: \(duration), URL: \(String(describing: url))")
        
        guard duration > 0.5, let fileURL = url else {
            print("[Recording] Skipped: duration too short or no URL")
            return
        }
        
        transcribeAndSend(url: fileURL)
    }
    
    private func updateMeter() {
        guard let recorder = audioRecorder else { return }
        recorder.updateMeters()
        
        let power = recorder.averagePower(forChannel: 0)
        // Convert dB to 0-1 range (dB typically ranges from -160 to 0)
        let normalizedPower = max(0, (power + 50) / 50)
        let level = CGFloat(Double(normalizedPower))
        
        // Shift values left and add new value
        waveformValues.removeFirst()
        waveformValues.append(max(0.1, min(1.0, level)))
    }
    
    // MARK: - Transcription
    
    private func transcribeAndSend(url: URL) {
        print("[Recording] Proceeding with transcription from \(url.path)")
        
        SFSpeechRecognizer.requestAuthorization { status in
            guard status == .authorized else {
                print("[Transcription] Not authorized: \(status)")
                return
            }
            
            guard let recognizer = SFSpeechRecognizer(), recognizer.isAvailable else {
                print("[Transcription] Recognizer not available")
                return
            }
            
            let request = SFSpeechURLRecognitionRequest(url: url)
            request.shouldReportPartialResults = false
            
            recognizer.recognitionTask(with: request) { result, error in
                if let error = error {
                    print("[Transcription] Error: \(error)")
                    return
                }
                
                guard let result = result, result.isFinal else { return }
                
                let transcribedText = result.bestTranscription.formattedString
                print("[Transcription] Result: \(transcribedText)")
                
                DispatchQueue.main.async {
                    self.draft = transcribedText
                    self.sendMessage()
                }
            }
        }
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

