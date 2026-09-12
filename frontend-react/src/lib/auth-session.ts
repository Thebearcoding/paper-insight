import { ApiError, fetchMe, migrateAnonymousData } from '@/lib/api';
import { clearPaperMarks, getAllPaperMarks, getUserId } from '@/lib/storage';
import type { AuthUser } from '@/types';

/** Only an explicit 401 is proof that the session has expired. */
export async function resolveAuthSession(): Promise<AuthUser | null> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return (await fetchMe()).user;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null;
      const transient = !(error instanceof ApiError) || error.status >= 500;
      if (!transient || attempt >= 1) throw error;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  }
}

export async function migrateLegacyLocalData(): Promise<void> {
  try {
    // localStorage can throw in privacy modes. Migration is optional and must
    // never turn a successful login into a logout or erase unsent marks.
    const anonymousUserId = getUserId();
    const paperMarks = getAllPaperMarks();
    await migrateAnonymousData(anonymousUserId, paperMarks);
    clearPaperMarks();
  } catch (error) {
    console.warn('Failed to migrate legacy local data', error);
  }
}
