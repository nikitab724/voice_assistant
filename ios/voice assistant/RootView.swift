//
//  RootView.swift
//  voice assistant
//
//  Created by Nikita Borisov on 12/11/25.
//

import SwiftUI

struct RootView: View {
    @StateObject private var authManager = AuthManager()
    
    var body: some View {
        Group {
            if authManager.isLoading {
                // Loading state
                ProgressView("Loading...")
                    .progressViewStyle(.circular)
            } else if authManager.isSignedIn {
                // Main app
                ContentView()
                    .environmentObject(authManager)
            } else {
                // Sign in screen
                SignInView()
                    .environmentObject(authManager)
            }
        }
    }
}

struct SignInView: View {
    @EnvironmentObject var authManager: AuthManager
    @State private var isSigningIn = false
    
    var body: some View {
        VStack(spacing: 32) {
            Spacer()
            
            // App icon/logo area
            VStack(spacing: 16) {
                Image(systemName: "waveform.circle.fill")
                    .font(.system(size: 80))
                    .foregroundStyle(.blue)
                
                Text("Voice Assistant")
                    .font(.largeTitle)
                    .fontWeight(.bold)
                
                Text("Sign in to access your calendar\nand start chatting")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            
            Spacer()
            
            // Error message
            if let error = authManager.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
            }
            
            // Google Sign In button
            Button {
                isSigningIn = true
                Task {
                    await authManager.signInWithGoogle()
                    isSigningIn = false
                }
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "g.circle.fill")
                        .font(.title2)
                    Text("Sign in with Google")
                        .fontWeight(.medium)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(Color(.systemBackground))
                .foregroundStyle(.primary)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color(.systemGray4), lineWidth: 1)
                )
            }
            .disabled(isSigningIn)
            .padding(.horizontal, 32)
            
            if isSigningIn {
                ProgressView()
                    .padding(.top, 8)
            }
            
            Spacer()
                .frame(height: 60)
        }
        .padding()
    }
}

#Preview {
    RootView()
}

