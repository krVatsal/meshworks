"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import axios from "axios";

export interface UserSubscription {
  plan_id: string;
  status: string;
  started_at: string;
  expires_at?: string | null;
}

export interface UserUsage {
  current_period_start: string;
  current_period_end: string;
  generations_count: number;
  generations_limit: number;
}

export interface User {
  id: string;
  email: string;
  name: string;
  picture?: string;
  role: string;
  institution_id?: string | null;
  subscription: UserSubscription;
  usage: UserUsage;
  created_at: string;
  last_login_at: string;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  loginModalOpen: boolean;
  setLoginModalOpen: (open: boolean) => void;
  quotaModalOpen: boolean;
  setQuotaModalOpen: (open: boolean) => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
  handleGoogleCredential: (credential: string) => Promise<void>;
  isQuotaExceeded: boolean;
  isMaster: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const TOKEN_KEY = "meshworks_token";
const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loginModalOpen, setLoginModalOpen] = useState<boolean>(false);
  const [quotaModalOpen, setQuotaModalOpen] = useState<boolean>(false);

  // Configure axios authorization header
  const updateAxiosAuth = (authToken: string | null) => {
    if (authToken) {
      axios.defaults.headers.common["Authorization"] = `Bearer ${authToken}`;
    } else {
      delete axios.defaults.headers.common["Authorization"];
    }
  };

  // Re-fetch user profile and quota count from backend
  const refreshUser = useCallback(async () => {
    const savedToken = localStorage.getItem(TOKEN_KEY);
    if (!savedToken) {
      setUser(null);
      setToken(null);
      updateAxiosAuth(null);
      setIsLoading(false);
      return;
    }

    try {
      updateAxiosAuth(savedToken);
      const res = await axios.get<User>("/api/auth/me");
      setUser(res.data);
      setToken(savedToken);
    } catch (err: any) {
      console.warn("[Auth] Session expired or invalid:", err?.response?.status);
      localStorage.removeItem(TOKEN_KEY);
      updateAxiosAuth(null);
      setUser(null);
      setToken(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Handle Google OAuth ID token exchange
  const handleGoogleCredential = async (credential: string) => {
    setIsLoading(true);
    try {
      const res = await axios.post<{ access_token: string; user: User }>("/api/auth/google", {
        id_token: credential,
      });

      const { access_token, user: userData } = res.data;
      localStorage.setItem(TOKEN_KEY, access_token);
      updateAxiosAuth(access_token);
      setToken(access_token);
      setUser(userData);
      setLoginModalOpen(false);
    } catch (err: any) {
      console.error("[Auth] Google sign-in failed:", err);
      alert("Sign-in failed. Please ensure Google OAuth credentials are valid.");
    } finally {
      setIsLoading(false);
    }
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    updateAxiosAuth(null);
    setUser(null);
    setToken(null);
  };

  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  const isMaster = Boolean(
    user && (user.role === "master" || user.email?.toLowerCase() === "prompt2mesh@gmail.com")
  );

  const isQuotaExceeded = Boolean(
    user &&
    !isMaster &&
    user.subscription?.plan_id === "free" &&
    user.usage?.generations_count >= user.usage?.generations_limit
  );

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        loginModalOpen,
        setLoginModalOpen,
        quotaModalOpen,
        setQuotaModalOpen,
        logout,
        refreshUser,
        handleGoogleCredential,
        isQuotaExceeded,
        isMaster,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
