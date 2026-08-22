# Tank3D Android (Google Play)

Готовый Android-проект для публикации игры **Tank3D** в Google Play.
Игра открывается в `WebView` из файла `app/src/main/assets/index.html`.

---

## Что уже подготовлено

- Release-сборка включена (`minifyEnabled true`, `shrinkResources true`)
- Подпись релиза через `key.properties`
- Версия приложения обновлена до:
  - `versionCode 2`
  - `versionName "1.0.1"`
- Базовые security-настройки для WebView усилены:
  - `allowUniversalAccessFromFileURLs = false`
  - `allowFileAccessFromFileURLs = false`
  - `mixedContentMode = MIXED_CONTENT_NEVER_ALLOW`
  - third-party cookies выключены
- Секреты и keystore исключены из git (`.gitignore`)

---

## Требования

- Android Studio (или CLI Gradle)
- JDK 17
- Android SDK 34
- Google Play Developer Account

---

## 1) Проверь, что игра в assets актуальная

Главный файл игры должен быть здесь:

`app/src/main/assets/index.html`

Если редактировал корневой `index.html`, скопируй его в assets перед сборкой.

---

## 2) Создай keystore для релиза

Из папки `android_webview_app`:

```bash
keytool -genkeypair -v -keystore release-keystore.jks -alias tank3d_release -keyalg RSA -keysize 2048 -validity 10000
```

---

## 3) Создай `key.properties`

Скопируй шаблон:

`key.properties.example` → `key.properties`

Заполни своими данными:

```properties
storeFile=release-keystore.jks
storePassword=YOUR_STORE_PASSWORD
keyAlias=tank3d_release
keyPassword=YOUR_KEY_PASSWORD
```

⚠️ `key.properties` и `.jks` не коммитить в git.

---

## 4) Собери AAB для Google Play

### Вариант A (рекомендуется): через Android Studio

1. Открой папку `android_webview_app` в Android Studio
2. Дождись Gradle Sync
3. Меню: **Build → Generate Signed Bundle / APK**
4. Выбери **Android App Bundle (AAB)**
5. Укажи свой keystore и собери релиз

Готовый файл:

`app/build/outputs/bundle/release/app-release.aab`

### Вариант B (CLI, если есть Gradle Wrapper)

Если в проекте есть `gradlew.bat`, можно собрать так:

```bash
gradlew.bat bundleRelease
```

---

## 5) Загрузка в Google Play Console

1. Открой Play Console
2. Создай приложение (или выбери существующее)
3. Перейди в **Production** (или Internal testing)
4. Нажми **Create new release**
5. Загрузи `app-release.aab`
6. Заполни release notes
7. Отправь релиз на проверку

---

## 6) Что обязательно заполниь в Play Console

- App name, short/long description
- Иконка 512x512
- Feature Graphic 1024x500
- Скриншоты телефона
- Privacy Policy URL
- Data safety form
- Content rating questionnaire
- Target audience

---

## Полезно перед релизом

- Повышай `versionCode` при каждом новом релизе
- Проверь работу на реальном устройстве
- Убедись, что все внешние ссылки в игре открываются корректно
- Если не нужен HTTP-трафик, можно позже убрать `usesCleartextTraffic="true"`



