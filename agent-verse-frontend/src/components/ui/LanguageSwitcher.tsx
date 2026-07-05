import { useTranslation } from 'react-i18next';

const LANGUAGES = [
  { code: 'en', label: 'EN', name: 'English' },
  { code: 'hi', label: 'हि', name: 'हिन्दी' },
];

export function LanguageSwitcher() {
  const { i18n } = useTranslation();
  return (
    <div className="flex items-center gap-1" role="group" aria-label="Language selection">
      {LANGUAGES.map(lang => (
        <button
          key={lang.code}
          onClick={() => i18n.changeLanguage(lang.code)}
          title={lang.name}
          aria-label={`Switch to ${lang.name}`}
          aria-pressed={i18n.language === lang.code}
          className={`px-2 py-1 text-xs rounded-md transition-colors ${
            i18n.language === lang.code
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted'
          }`}
        >
          {lang.label}
        </button>
      ))}
    </div>
  );
}
