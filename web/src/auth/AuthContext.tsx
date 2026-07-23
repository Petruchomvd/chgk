import { createContext, useContext } from 'react'
import type { AuthUser } from '@/lib/api'

interface AuthContextValue {
  user: AuthUser
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({
  user,
  logout,
  children,
}: AuthContextValue & { children: React.ReactNode }) {
  return (
    <AuthContext.Provider value={{ user, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth доступен только внутри AuthProvider')
  return value
}
