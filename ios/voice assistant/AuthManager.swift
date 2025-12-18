//
//  AuthManager.swift
//  voice assistant
//
//  Created by Nikita Borisov on 12/11/25.
//

import Foundation
import Combine
import FirebaseAuth
import FirebaseCore
import GoogleSignIn

@MainActor
class AuthManager: ObservableObject {
    @Published var user: User?
    @Published var isSignedIn = false
    @Published var isLoading = true
    @Published var errorMessage: String?
    
    // Store Google tokens for calendar access
    @Published var googleAccessToken: String?
    @Published var googleRefreshToken: String?
    
    private var authStateHandle: AuthStateDidChangeListenerHandle?
    
    init() {
        // Listen for auth state changes
        authStateHandle = Auth.auth().addStateDidChangeListener { [weak self] _, user in
            Task { @MainActor in
                self?.user = user
                self?.isSignedIn = user != nil
                self?.isLoading = false
            }
        }
    }
    
    func signInWithGoogle() async {
        guard let clientID = FirebaseApp.app()?.options.clientID else {
            errorMessage = "Missing Firebase client ID"
            return
        }
        
        let config = GIDConfiguration(clientID: clientID)
        GIDSignIn.sharedInstance.configuration = config
        
        // Get the root view controller
        guard let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              let rootViewController = windowScene.windows.first?.rootViewController else {
            errorMessage = "No root view controller found"
            return
        }
        
        do {
            // Request calendar + gmail scope along with standard scopes (incremental auth)
            let result = try await GIDSignIn.sharedInstance.signIn(
                withPresenting: rootViewController,
                hint: nil,
                additionalScopes: [
                    "https://www.googleapis.com/auth/calendar",
                    // Needed for marking emails read / labeling
                    "https://www.googleapis.com/auth/gmail.modify",
                    // Needed for drafting and sending emails
                    "https://www.googleapis.com/auth/gmail.compose",
                    "https://www.googleapis.com/auth/gmail.send",
                ]
            )
            
            let user = result.user
            guard let idToken = user.idToken?.tokenString else {
                errorMessage = "Missing ID token"
                return
            }
            
            // Store Google tokens for calendar API access
            self.googleAccessToken = user.accessToken.tokenString
            self.googleRefreshToken = user.refreshToken.tokenString
            
            // Create Firebase credential
            let credential = GoogleAuthProvider.credential(
                withIDToken: idToken,
                accessToken: user.accessToken.tokenString
            )
            
            // Sign in to Firebase
            try await Auth.auth().signIn(with: credential)
            
            print("[Auth] Signed in as: \(user.profile?.email ?? "unknown")")
            print("[Auth] Access token: \(String(describing: googleAccessToken?.prefix(20)))...")
            
        } catch {
            errorMessage = error.localizedDescription
            print("[Auth] Error: \(error)")
        }
    }
    
    func signOut() {
        do {
            try Auth.auth().signOut()
            GIDSignIn.sharedInstance.signOut()
            googleAccessToken = nil
            googleRefreshToken = nil
            print("[Auth] Signed out")
        } catch {
            errorMessage = error.localizedDescription
        }
    }
    
    /// Refresh Google access token if needed
    func refreshGoogleToken() async -> String? {
        guard let currentUser = GIDSignIn.sharedInstance.currentUser else {
            return nil
        }
        
        do {
            let result = try await currentUser.refreshTokensIfNeeded()
            self.googleAccessToken = result.accessToken.tokenString
            return result.accessToken.tokenString
        } catch {
            print("[Auth] Failed to refresh token: \(error)")
            return nil
        }
    }
}

