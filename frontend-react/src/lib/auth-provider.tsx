import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import {
  githubAuthUrl,
  login as loginRequest,
  logout as logoutRequest,
} from '@/lib/api';
import { AuthContext, type AuthContextValue } from '@/lib/auth';
import { migrateLegacyLocalData, resolveAuthSession } from '@/lib/auth-session';
import type { AuthUser } from '@/types';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    setIsLoading(true);
    try {
      const nextUser = await resolveAuthSession();
      if (requestVersion.current !== version) return;
      setUser(nextUser);
      setError(null);
      if (nextUser) void migrateLegacyLocalData();
    } catch {
      if (requestVersion.current === version) {
        setError('暂时无法确认登录状态，请检查网络后重试。不会因此清除你的登录会话。');
      }
    } finally {
      if (requestVersion.current === version) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => { requestVersion.current += 1; };
  }, [refresh]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    isLoading,
    login: async (email: string, password: string) => {
      const version = ++requestVersion.current;
      try {
        const payload = await loginRequest(email, password);
        if (requestVersion.current !== version) return;
        setUser(payload.user);
        setError(null);
        void migrateLegacyLocalData();
      } finally {
        if (requestVersion.current === version) setIsLoading(false);
      }
    },
    loginWithGithub: (nextPath = '/') => {
      window.location.href = githubAuthUrl(nextPath);
    },
    logout: async () => {
      const version = ++requestVersion.current;
      try {
        await logoutRequest();
      } finally {
        if (requestVersion.current === version) setIsLoading(false);
      }
      requestVersion.current += 1;
      setUser(null);
      setError(null);
      setIsLoading(false);
    },
    refresh,
  }), [isLoading, user, refresh]);

  return (
    <AuthContext.Provider value={value}>
      {error ? (
        <div role="alert" className="m-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-900">
          {error}
          <button type="button" disabled={isLoading} onClick={() => void refresh()} className="ml-3 underline">
            {isLoading ? '正在重试…' : '重新检查'}
          </button>
        </div>
      ) : null}
      {/* Do not redirect a private deep link while its identity is unknown. */}
      {(!error || user) ? children : null}
    </AuthContext.Provider>
  );
}
