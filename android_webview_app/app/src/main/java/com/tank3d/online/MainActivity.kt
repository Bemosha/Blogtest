package com.tank3d.online

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

class MainActivity : AppCompatActivity(), RecognitionListener {

    private lateinit var statusText: TextView
    private lateinit var heardText: TextView
    private lateinit var answerText: TextView
    private lateinit var toggleButton: Button

    private var speechRecognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null

    private val handler = Handler(Looper.getMainLooper())
    private var listeningEnabled = false
    private var waitingForQuestion = false
    private var isSpeaking = false

    private val permissionCode = 1001

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.statusText)
        heardText = findViewById(R.id.heardText)
        answerText = findViewById(R.id.answerText)
        toggleButton = findViewById(R.id.toggleButton)

        initTts()
        initSpeechRecognizer()

        toggleButton.setOnClickListener {
            if (!listeningEnabled) {
                ensureAudioPermissionAndStart()
            } else {
                stopAssistant()
            }
        }
    }

    private fun initTts() {
        tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale("ru", "RU")
                statusText.text = "Готов. Нажми «Запустить Фрэнди»."
            } else {
                statusText.text = "Ошибка инициализации озвучки"
            }
        }
    }

    private fun initSpeechRecognizer() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            statusText.text = "Распознавание речи недоступно на устройстве"
            return
        }
        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
        speechRecognizer?.setRecognitionListener(this)
    }

    private fun ensureAudioPermissionAndStart() {
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        if (granted) {
            startAssistant()
        } else {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                permissionCode
            )
        }
    }

    private fun startAssistant() {
        listeningEnabled = true
        waitingForQuestion = false
        toggleButton.text = "Остановить"
        statusText.text = "Слушаю... скажи: «привет фрэнди»"
        startListening()
    }

    private fun stopAssistant() {
        listeningEnabled = false
        waitingForQuestion = false
        isSpeaking = false
        toggleButton.text = "Запустить Фрэнди"
        statusText.text = "Остановлено"
        speechRecognizer?.stopListening()
    }

    private fun startListening() {
        if (!listeningEnabled || isSpeaking) return

        val intent = RecognizerIntent().apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ru-RU")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
        }

        try {
            speechRecognizer?.startListening(intent)
        } catch (_: Exception) {
            scheduleRestart(500)
        }
    }

    private fun scheduleRestart(delayMs: Long = 350) {
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({ startListening() }, delayMs)
    }

    private fun isWakePhrase(text: String): Boolean {
        val low = text.lowercase(Locale.getDefault())
        return low.contains("привет френди") || low.contains("привет фрэнди") || low.contains("хей френди")
    }

    private fun processRecognizedText(text: String) {
        if (!listeningEnabled) return
        heardText.text = "Вы: $text"

        if (!waitingForQuestion) {
            if (isWakePhrase(text)) {
                waitingForQuestion = true
                speak("Привет, что хотел?") {
                    statusText.text = "Задай вопрос"
                    scheduleRestart(250)
                }
            } else {
                statusText.text = "Ожидаю фразу: «привет фрэнди»"
                scheduleRestart(150)
            }
            return
        }

        statusText.text = "Думаю над ответом..."
        waitingForQuestion = false
        answerQuestion(text)
    }

    private fun answerQuestion(question: String) {
        Thread {
            val answer = fetchAnswer(question)
            runOnUiThread {
                answerText.text = "Фрэнди: $answer"
                speak(answer) {
                    statusText.text = "Снова скажи: «привет фрэнди»"
                    scheduleRestart(300)
                }
            }
        }.start()
    }

    private fun fetchAnswer(question: String): String {
        if (BuildConfig.OPENAI_API_KEY.isBlank()) {
            return "Чтобы отвечать максимально точно, добавь OPENAI_API_KEY в Gradle. Сейчас ключ не задан."
        }

        return try {
            val conn = URL("https://api.openai.com/v1/responses").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Authorization", "Bearer ${BuildConfig.OPENAI_API_KEY}")
            conn.setRequestProperty("Content-Type", "application/json")
            conn.connectTimeout = 30000
            conn.readTimeout = 30000
            conn.doOutput = true

            val body = JSONObject().apply {
                put("model", BuildConfig.OPENAI_MODEL)
                put(
                    "input",
                    JSONArray().apply {
                        put(
                            JSONObject().apply {
                                put("role", "system")
                                put("content", "Ты голосовой помощник Фрэнди. Отвечай кратко и максимально правильно на русском языке.")
                            }
                        )
                        put(
                            JSONObject().apply {
                                put("role", "user")
                                put("content", question)
                            }
                        )
                    }
                )
                put("max_output_tokens", 220)
            }

            OutputStreamWriter(conn.outputStream).use { it.write(body.toString()) }

            val responseText = BufferedReader(InputStreamReader(
                if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
            )).use { it.readText() }

            parseOpenAiAnswer(responseText)
                ?: "Не смог дать точный ответ. Спроси по‑другому."
        } catch (_: Exception) {
            "Ошибка сети. Проверь интернет и попробуй снова."
        }
    }

    private fun parseOpenAiAnswer(jsonText: String): String? {
        return try {
            val root = JSONObject(jsonText)
            val outputText = root.optString("output_text", "")
            if (outputText.isNotBlank()) return outputText

            val output = root.optJSONArray("output") ?: return null
            val sb = StringBuilder()
            for (i in 0 until output.length()) {
                val item = output.optJSONObject(i) ?: continue
                if (item.optString("type") != "message") continue
                val content = item.optJSONArray("content") ?: continue
                for (j in 0 until content.length()) {
                    val chunk = content.optJSONObject(j) ?: continue
                    if (chunk.optString("type") == "output_text") {
                        val part = chunk.optString("text")
                        if (part.isNotBlank()) sb.append(part).append(' ')
                    }
                }
            }
            sb.toString().trim().ifBlank { null }
        } catch (_: Exception) {
            null
        }
    }

    private fun speak(text: String, onDone: () -> Unit) {
        isSpeaking = true
        tts?.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit

            override fun onDone(utteranceId: String?) {
                runOnUiThread {
                    isSpeaking = false
                    onDone()
                }
            }

            override fun onError(utteranceId: String?) {
                runOnUiThread {
                    isSpeaking = false
                    onDone()
                }
            }
        })
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "frendy_utterance")
    }

    override fun onReadyForSpeech(params: Bundle?) = Unit
    override fun onBeginningOfSpeech() = Unit
    override fun onRmsChanged(rmsdB: Float) = Unit
    override fun onBufferReceived(buffer: ByteArray?) = Unit
    override fun onEndOfSpeech() = Unit

    override fun onError(error: Int) {
        if (!listeningEnabled || isSpeaking) return
        scheduleRestart(400)
    }

    override fun onResults(results: Bundle?) {
        val text = results
            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            ?.firstOrNull()
            ?.trim()
            .orEmpty()

        if (text.isNotBlank()) {
            processRecognizedText(text)
        } else {
            scheduleRestart(250)
        }
    }

    override fun onPartialResults(partialResults: Bundle?) = Unit
    override fun onEvent(eventType: Int, params: Bundle?) = Unit

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == permissionCode && grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startAssistant()
        } else {
            statusText.text = "Нужен доступ к микрофону"
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacksAndMessages(null)
        speechRecognizer?.destroy()
        tts?.stop()
        tts?.shutdown()
    }
}
