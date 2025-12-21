import SwiftUI

struct HistoryDrawer: View {
    @Binding var isOpen: Bool
    let messages: [ChatMessage]
    let showTypingIndicator: Bool
    
    @State private var dragOffset: CGFloat = 0
    private let threshold: CGFloat = 100
    
    var body: some View {
        GeometryReader { geo in
            let drawerHeight = geo.size.height * 0.8
            
            VStack(spacing: 0) {
                // Main Content
                VStack(spacing: 0) {
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
                                
                                Color.clear
                                    .frame(height: 1)
                                    .id("bottom")
                            }
                            .padding(.vertical, 20)
                        }
                        .scrollDismissesKeyboard(.interactively)
                        .onChange(of: messages.count) {
                            scrollToBottom(proxy: proxy)
                        }
                        .onChange(of: showTypingIndicator) {
                            scrollToBottom(proxy: proxy)
                        }
                    }
                }
                .frame(height: drawerHeight)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 32, style: .continuous))
                .shadow(color: .black.opacity(0.2), radius: 20)
                
                // Pull Handle
                Capsule()
                    .fill(.secondary.opacity(0.5))
                    .frame(width: 40, height: 5)
                    .padding(.top, 12)
                    .padding(.bottom, 20)
                    .contentShape(Rectangle())
                    .onTapGesture {
                        withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                            isOpen = false
                        }
                    }
            }
            .offset(y: isOpen ? 0 : -drawerHeight - 100)
            .offset(y: dragOffset)
            .gesture(
                DragGesture()
                    .onChanged { value in
                        if value.translation.height < 0 {
                            dragOffset = value.translation.height
                        }
                    }
                    .onEnded { value in
                        if value.translation.height < -threshold {
                            withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                isOpen = false
                                dragOffset = 0
                            }
                        } else {
                            withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                dragOffset = 0
                            }
                        }
                    }
            )
        }
        .ignoresSafeArea(edges: .bottom)
    }
    
    private func chatBubble(for message: ChatMessage) -> some View {
        let maxBubbleWidth: CGFloat = 280
        
        return HStack {
            if message.isUser { Spacer(minLength: 60) }
            
            Group {
                if message.isUser {
                    Text(message.text)
                } else {
                    Text(.init(message.text))
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(
                message.isUser
                ? Color.blue.opacity(0.8)
                : Color.white.opacity(0.15)
            )
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
            .frame(maxWidth: maxBubbleWidth, alignment: message.isUser ? .trailing : .leading)
            
            if !message.isUser { Spacer(minLength: 60) }
        }
        .padding(.horizontal, 16)
    }
    
    private func scrollToBottom(proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.3)) {
            if showTypingIndicator {
                proxy.scrollTo("typing", anchor: .bottom)
            } else if let last = messages.last {
                proxy.scrollTo(last.id, anchor: .bottom)
            }
        }
    }
}
