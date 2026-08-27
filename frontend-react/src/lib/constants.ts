import type { ConferenceDefinition, ConferenceSlug } from '@/types';

export const CONFERENCES: ConferenceDefinition[] = [
  {
    id: 'aaai_2026',
    name: 'AAAI 2026',
    fullName: 'AAAI Conference on Artificial Intelligence',
    year: 2026,
    accentClass: 'from-[#2563eb] via-[#7c3aed] to-[#ec4899]',
  },
  {
    id: 'kdd_2026',
    name: 'KDD 2026',
    fullName: 'ACM SIGKDD Conference on Knowledge Discovery and Data Mining',
    year: 2026,
    accentClass: 'from-[#dc2626] via-[#f59e0b] to-[#16a34a]',
  },
  {
    id: 'sigir_2026',
    name: 'SIGIR 2026',
    fullName: 'ACM SIGIR Conference on Research and Development in Information Retrieval',
    year: 2026,
    accentClass: 'from-[#0891b2] via-[#2563eb] to-[#f97316]',
  },
  {
    id: 'acl_2026',
    name: 'ACL 2026',
    fullName: 'Annual Meeting of the Association for Computational Linguistics',
    year: 2026,
    accentClass: 'from-[#14b8a6] via-[#38bdf8] to-[#6366f1]',
  },
  {
    id: 'iclr_2026',
    name: 'ICLR 2026',
    fullName: 'International Conference on Learning Representations',
    year: 2026,
    accentClass: 'from-[#ffb347] via-[#ffd56b] to-[#ff8f5a]',
  },
  {
    id: 'chi_2026',
    name: 'CHI 2026',
    fullName: 'Conference on Human Factors in Computing Systems',
    year: 2026,
    accentClass: 'from-[#f26d6d] via-[#ff9f7a] to-[#ffd166]',
  },
  {
    id: 'cvpr_2026',
    name: 'CVPR 2026',
    fullName: 'Conference on Computer Vision and Pattern Recognition',
    year: 2026,
    accentClass: 'from-[#0ea5e9] via-[#22c55e] to-[#facc15]',
  },
  {
    id: 'aaai_2025',
    name: 'AAAI 2025',
    fullName: 'AAAI Conference on Artificial Intelligence',
    year: 2025,
    accentClass: 'from-[#1d4ed8] via-[#7c3aed] to-[#db2777]',
  },
  {
    id: 'iclr_2025',
    name: 'ICLR 2025',
    fullName: 'International Conference on Learning Representations',
    year: 2025,
    accentClass: 'from-[#fb923c] via-[#facc15] to-[#f97316]',
  },
  {
    id: 'acl_2025',
    name: 'ACL 2025',
    fullName: 'Annual Meeting of the Association for Computational Linguistics',
    year: 2025,
    accentClass: 'from-[#0d9488] via-[#0ea5e9] to-[#4f46e5]',
  },
  {
    id: 'cvpr_2025',
    name: 'CVPR 2025',
    fullName: 'Conference on Computer Vision and Pattern Recognition',
    year: 2025,
    accentClass: 'from-[#0284c7] via-[#16a34a] to-[#eab308]',
  },
  {
    id: 'iccv_2025',
    name: 'ICCV 2025',
    fullName: 'International Conference on Computer Vision',
    year: 2025,
    accentClass: 'from-[#f59e0b] via-[#f97316] to-[#dc2626]',
  },
  {
    id: 'kdd_2025',
    name: 'KDD 2025',
    fullName: 'ACM SIGKDD Conference on Knowledge Discovery and Data Mining',
    year: 2025,
    accentClass: 'from-[#b91c1c] via-[#d97706] to-[#15803d]',
  },
  {
    id: 'sigir_2025',
    name: 'SIGIR 2025',
    fullName: 'ACM SIGIR Conference on Research and Development in Information Retrieval',
    year: 2025,
    accentClass: 'from-[#0e7490] via-[#1d4ed8] to-[#ea580c]',
  },
  {
    id: 'chi_2025',
    name: 'CHI 2025',
    fullName: 'Conference on Human Factors in Computing Systems',
    year: 2025,
    accentClass: 'from-[#e11d48] via-[#fb7185] to-[#f59e0b]',
  },
  {
    id: 'neurips_2025',
    name: 'NeurIPS 2025',
    fullName: 'Neural Information Processing Systems',
    year: 2025,
    accentClass: 'from-[#7c6cff] via-[#9c8cff] to-[#5f8bff]',
  },
  {
    id: 'ijcai_2025',
    name: 'IJCAI 2025',
    fullName: 'International Joint Conference on Artificial Intelligence',
    year: 2025,
    accentClass: 'from-[#be123c] via-[#7c3aed] to-[#0d9488]',
  },
  {
    id: 'icml_2025',
    name: 'ICML 2025',
    fullName: 'International Conference on Machine Learning',
    year: 2025,
    accentClass: 'from-[#4cb782] via-[#8fd694] to-[#cde77f]',
  },
];

export const CONFERENCE_MAP = CONFERENCES.reduce<Record<ConferenceSlug, ConferenceDefinition>>(
  (acc, conference) => {
    acc[conference.id as ConferenceSlug] = conference;
    return acc;
  },
  {} as Record<ConferenceSlug, ConferenceDefinition>,
);

export function getConferenceDefinition(venue: string): ConferenceDefinition | null {
  return CONFERENCE_MAP[venue as ConferenceSlug] ?? null;
}

export function getConferenceSlugFromVenue(venue?: string | null): ConferenceSlug | null {
  if (!venue) {
    return null;
  }

  const normalizedVenue = venue.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const year = normalizedVenue.match(/\b(20\d{2})\b/)?.[1] ?? null;

  const conference = CONFERENCES.find((candidate) => {
    const normalizedName = candidate.name.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    const acronym = normalizedName.split(' ')[0];

    return normalizedVenue.includes(normalizedName) || (
      year === String(candidate.year) && normalizedVenue.split(' ').includes(acronym)
    );
  });

  return (conference?.id as ConferenceSlug | undefined) ?? null;
}

export function buildConferenceKeywordSearchPath(venue: string | null | undefined, keyword: string): string | null {
  const conferenceSlug = getConferenceSlugFromVenue(venue);
  const query = keyword.trim();

  if (!conferenceSlug || !query) {
    return null;
  }

  const params = new URLSearchParams({
    q: query,
    title: 'false',
    abstract: 'false',
    keywords: 'true',
  });

  return `/conference/${conferenceSlug}?${params.toString()}`;
}

export function buildPaperKeywordSearchPath(venue: string | null | undefined, keyword: string): string | null {
  const query = keyword.trim();
  if (!query) {
    return null;
  }

  const conferenceSearchPath = buildConferenceKeywordSearchPath(venue, query);
  if (conferenceSearchPath) {
    return conferenceSearchPath;
  }

  if (venue?.toLowerCase().includes('hugging face')) {
    const params = new URLSearchParams({
      q: query,
      title: 'false',
      abstract: 'false',
      keywords: 'true',
    });

    return `/hf-daily?${params.toString()}`;
  }

  return null;
}
