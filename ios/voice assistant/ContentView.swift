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
    
    func sendMessage(sessionId: String, message: String, userId: String?, googleAccessToken: String?) async throws -> ChatResponse {
        guard let url = URL(string: "\(baseURL)/api/chat") else {
            throw URLError(.badURL)
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        let body = ChatRequest(session_id: sessionId, message: message, user_id: userId, google_access_token: googleAccessToken)
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

// MARK: - Main Content View

struct ContentView: View {
    @EnvironmentObject var authManager: AuthManager
    
    private let chatClient = ChatClient()
    
    @State private var messages: [ChatMessage] = []
    @State private var draft = ""
    @State private var sessionId = UUID().uuidString
    @State private var isAwaitingResponse = false
    
    // Recording state
    @State private var isRecording = false
    @State private var recordingStart: Date?
    @State private var recordingElapsed: TimeInterval = 0
    @State private var recordingURL: URL?
    @State private var audioRecorder: AVAudioRecorder?
    @State private var waveformValues: [CGFloat] = Array(repeating: 0.2, count: 12)
    private var recordingTimer = Timer.publish(every: 0.05, on: .main, in: .common).autoconnect()
    
    // Audio playback
    @State private var audioPlayer: AVAudioPlayer?
    
    // Focus and keyboard
    @FocusState private var isComposerFocused: Bool
    
    var body: some View {
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
                            
                            if isAwaitingResponse {
                                HStack {
                                    TypingIndicatorView()
                                    Spacer()
                                }
                                .padding(.horizontal, 16)
                                .id("typing")
                            }
                        }
                        .padding(.vertical, 12)
                    }
                    .onChange(of: messages.count) { _, _ in
                        scrollToBottom(proxy: proxy)
                    }
                    .onChange(of: isAwaitingResponse) { _, _ in
                        scrollToBottom(proxy: proxy)
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
            
            Text(message.text)
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
                if isAwaitingResponse {
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
        
        let userMessage = ChatMessage(text: text, isUser: true)
        messages.append(userMessage)
        draft = ""
        isComposerFocused = false
        isAwaitingResponse = true
        
        Task {
            do {
                // Refresh token if needed before sending
                let token = await authManager.refreshGoogleToken()
                
                let response = try await chatClient.sendMessage(
                    sessionId: sessionId,
                    message: text,
                    userId: authManager.user?.uid,
                    googleAccessToken: token
                )
                
                await MainActor.run {
                    isAwaitingResponse = false
                    
                    if let responseText = response.text, !responseText.isEmpty {
                        let assistantMessage = ChatMessage(text: responseText, isUser: false)
                        messages.append(assistantMessage)
                        
                        // Play audio if provided
                        if let audioBase64 = response.audio {
                            playAudio(base64: audioBase64)
                        }
                    }
                }
            } catch {
                await MainActor.run {
                    isAwaitingResponse = false
                    let errorMessage = ChatMessage(text: "Error: \(error.localizedDescription)", isUser: false)
                    messages.append(errorMessage)
                }
            }
        }
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
    
    private func playAudio(base64: String) {
        guard let audioData = Data(base64Encoded: base64) else {
            print("[Audio] Failed to decode base64")
            return
        }
        
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)
            
            audioPlayer = try AVAudioPlayer(data: audioData)
            audioPlayer?.prepareToPlay()
            audioPlayer?.play()
            print("[Audio] Playing response audio")
        } catch {
            print("[Audio] Playback error: \(error)")
        }
    }
}

#Preview {
    ContentView()
        .environmentObject(AuthManager())
}

