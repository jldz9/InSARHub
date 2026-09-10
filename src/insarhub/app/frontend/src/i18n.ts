import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'
import zh from './locales/zh.json'

const STORAGE_KEY = 'insarhub_lang'
const saved = typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null

i18n
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
    },
    lng: saved === 'zh' || saved === 'en' ? saved : 'en',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })

export function setLanguage(lang: 'en' | 'zh') {
  i18n.changeLanguage(lang)
  localStorage.setItem(STORAGE_KEY, lang)
}

export default i18n
