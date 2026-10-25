# Assist Speaker ID dla Home Assistant: kto mówi do Assista

[English](README.md) · **Polski**

Assist zamienia mowę na tekst, ale nie wie, **kto** mówi. Po pytaniu „Jakie mam dzisiaj
zajęcia?” nie wiadomo, czyj plan lekcji podać. Assist Speaker ID rozpoznaje domownika po głosie
i przekazuje tę informację dalej: do agenta konwersacji, do modelu LLM albo do automatyzacji.

Repozytorium zawiera dwie części:

| Część | Katalog | Instalacja |
|---|---|---|
| Usługa **wyoming-speaker-id**: pośrednik Wyoming przed serwerem zamiany mowy na tekst (np. faster-whisper), który rozpoznaje mówcę | `speaker_id/` | dodatek HA (to repozytorium jako repozytorium dodatków) albo kontener Docker |
| Integracja **Assist Speaker ID**: encja zamiany mowy na tekst z mówcą, agent-opakowanie, sensor, akcje | `custom_components/speaker_id/` | HACS (repozytorium niestandardowe, typ: integracja) |

**Dokumentacja ze schematami** (dlaczego i jak to działa, instalacja, nagrywanie wzorców,
progi i kalibracja, korzystanie na co dzień, prywatność):
po polsku [docs/pl](https://witold-gren.github.io/home-assistant-assist-speaker-id/pl/),
in English [docs/en](https://witold-gren.github.io/home-assistant-assist-speaker-id/en/)
(pliki źródłowe: [docs/](docs/)).

## Jak to działa

```
satelita ──dźwięk──▶ HA: STT „Mowa na tekst z mówcą” (integracja Assist Speaker ID)
                         │ Wyoming
                         ▼
               wyoming-speaker-id ──dźwięk bez zmian──▶ Whisper ──▶ tekst
                 └─ model głosu CAM++ ─▶ wektor 192 liczb ─▶ porównanie ze wzorcami
                         │
                         ▼ transcript {text, speaker, speaker_score, …}
HA: wynik w tym samym przebiegu pipeline'u
  ├─ agent „Agent z mówcą” → dopisuje „The person speaking is Anna.” i przekazuje dalej
  ├─ get_speaker(hass) dla innych integracji (np. własnego agenta)
  └─ sensor „Ostatni mówca”, zdarzenie speaker_id_identified
```

- **Rozpoznanie:** model zamienia pierwsze sekundy mowy na wektor. Wzorzec osoby to średnia
  wektorów z jej nagrań. Wynik to podobieństwo cosinusowe. Osoba wygrywa tylko wtedy, gdy
  przekracza swój próg **i** wyprzedza drugą o margines; w przeciwnym razie mówca jest
  „nieznany”.
- **Bez dodatkowego opóźnienia:** wektor liczy się w czasie, gdy Whisper transkrybuje.
  Zmierzone na Jetson AGX Orin (CPU, 2 wątki): mediana bez zmian (1329 → 1330 ms), p90
  +95 ms.
- **Tekst jest zawsze ten sam, co z samego Whispera.** Zwykły klient Wyoming (np. wbudowana
  integracja Wyoming) pomija dodatkowe pola, więc usługa niczego nie psuje.
- **Wynik trafia do agenta konwersacji tego samego przebiegu pipeline'u** (`ContextVar`;
  HA wywołuje zamianę mowy na tekst i rozpoznanie intencji w jednym zadaniu, co sprawdza
  test `test_pipeline_passes_speaker_from_stt_to_agent`). Tekst wpisany ręcznie nie ma
  mówcy.

**Sprawdzone w praktyce:** u autora usługa działa na co dzień na **NVIDIA Jetson AGX Orin 64 GB**,
jako kontener obok faster-whisper, dla całej rodziny i 7 satelit, i działa bardzo dobrze.

### Wybór modelu (pomiary 2026-10-23)

Zestawy testowe: 6 polskich głosów Pipera × 12 zdań oraz nagrania 3 osób z przykładów
sherpa-onnx. Dokładność liczona metodą „zostaw jedno nagranie”; EER to równowaga
fałszywych akceptacji i fałszywych odrzuceń.

| Model (sherpa-onnx) | Wymiar | 3 s mowy | Piper: dokładność / EER | Ludzie: dokładność / EER |
|---|---|---|---|---|
| **3dspeaker CAM++ zh_en „advanced”** (domyślny) | 192 | 91 ms | 100% / 0,2% | 100% / 0% |
| NeMo TitaNet-small | 192 | 59 ms | 100% / 0,9% | 100% / 0% |
| 3dspeaker ERes2Net (VoxCeleb) | 192 | 218 ms | 100% / 1,9% | 100% / 0% |
| WeSpeaker ResNet34-LM | 256 | 257 ms | 98,6% / 12,4% | 92,9% / 15,4% |
| 3dspeaker CAM++ (VoxCeleb) | 512 | 90 ms | 68,1% / 40,8% | 71,4% / 37,7% |

Przez cały łańcuch (Piper → wyoming-speaker-id → Whisper) wszystkie 23 wypowiedzi
z co najmniej 1 s mowy zostały przypisane właściwej osobie, z wynikami 0,76–0,92.
Wypowiedź z 0,6 s mowy dała „nieznany”, zgodnie z założeniem.

## Instalacja

### Usługa jako dodatek HA

1. Ustawienia → Dodatki → Sklep → ⋮ → Repozytoria: dodaj
   `https://github.com/witold-gren/home-assistant-assist-speaker-id`.
2. Zainstaluj **Assist Speaker ID (rozpoznawanie mówcy)**. W konfiguracji ustaw **Serwer zamiany
   mowy na tekst**: `tcp://core-whisper:10300` dla dodatku Whisper albo adres własnego
   serwera.
3. Uruchom dodatek. Ogłosi się Home Assistantowi (z losowym tokenem API), a HA zaproponuje
   integrację.

### Usługa jako kontener (np. obok Whispera na innym komputerze)

```sh
docker build -t wyoming-speaker-id speaker_id/
docker run -d --name speaker-id --restart unless-stopped \
  -p 10350:10350 -p 10351:10351 -v speaker-id-data:/data \
  -e SPEAKER_ID_UPSTREAM=tcp://192.168.1.10:10300 \
  -e SPEAKER_ID_API_TOKEN=długi-losowy-token \
  wyoming-speaker-id
```

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `SPEAKER_ID_UPSTREAM` | (wymagane) | serwer Wyoming zamiany mowy na tekst |
| `SPEAKER_ID_THRESHOLD` / `SPEAKER_ID_MARGIN` | 0.5 / 0.05 | wspólny próg i przewaga nad drugą osobą |
| `SPEAKER_ID_MIN_SPEECH` / `SPEAKER_ID_MAX_SPEECH` | 1.0 / 8.0 s | poniżej minimum mówca to „nieznany”; ile mowy liczyć |
| `SPEAKER_ID_MODEL` | CAM++ zh_en | nazwa modelu sherpa-onnx (pobierany raz) albo ścieżka |
| `SPEAKER_ID_API_TOKEN` | brak | token API HTTP (bez niego trzymaj port 10351 w sieci domowej) |
| `SPEAKER_ID_THREADS`, `SPEAKER_ID_DEBUG` | 2, 0 | wątki modelu, szczegółowy dziennik |

### Usługa w docker compose (bez dodatku)

Tak działa to u autora: kontener obok Whispera na Jetsonie AGX Orin, poza komputerem z HA.
Dźwięk idzie wtedy z HA do usługi, a dalej do Whispera już w obrębie jednej maszyny.

1. Obok swojego `docker-compose.yaml` pobierz repozytorium i utwórz token API:

   ```sh
   git clone https://github.com/witold-gren/home-assistant-assist-speaker-id
   echo "SPEAKER_ID_API_TOKEN=$(openssl rand -hex 24)" >> .env
   ```

2. Dopisz usługę do `docker-compose.yaml` (tu z przykładowym Whisperem; jeśli masz już
   własnego, zostaw go i zmień tylko `SPEAKER_ID_UPSTREAM` na jego nazwę i port):

   ```yaml
   services:
     whisper:
       image: rhasspy/wyoming-whisper
       command: --model medium-int8 --language pl
       ports: ["10300:10300"]
       volumes: ["./whisper:/data"]
       restart: unless-stopped

     speaker-id:
       build: ./home-assistant-assist-speaker-id/speaker_id
       image: local/wyoming-speaker-id:0.1.0
       container_name: speaker-id
       depends_on: [whisper]
       ports:
         - "10350:10350"          # Wyoming: zamiana mowy na tekst z mówcą
         - "10351:10351"          # API HTTP: wzorce głosu, kalibracja
       volumes:
         - ./speaker-id/data:/data          # speakers.json: same wektory, bez nagrań
         - /etc/localtime:/etc/localtime:ro
       environment:
         SPEAKER_ID_UPSTREAM: tcp://whisper:10300
         SPEAKER_ID_API_TOKEN: ${SPEAKER_ID_API_TOKEN}
       restart: unless-stopped
   ```

3. Zbuduj i uruchom: `docker compose up -d --build speaker-id`. Za pierwszym razem usługa
   pobierze model (ok. 30 MB) do `/data`. Sprawdzenie: `docker compose logs -f speaker-id`
   oraz `curl -H "Authorization: Bearer <token>" http://192.168.1.10:10351/api/info`.
4. W HA dodaj integrację **Assist Speaker ID** ręcznie (rozdział [Integracja](#integracja)):
   host `192.168.1.10`, porty 10350 i 10351, token z `.env`. Integracja Wyoming dla samego
   Whispera może zostać, ale w asystentach głosowych wybierz „Mowa na tekst z mówcą”.
5. Aktualizacja: `git -C home-assistant-assist-speaker-id pull && docker compose up -d --build speaker-id`.
   Wzorce głosu zostają w `./speaker-id/data`.

Model głosu liczy na procesorze, więc kontener nie potrzebuje GPU ani `runtime: nvidia`,
także na Jetsonie (tam GPU zostaje dla Whispera i Ollamy). Pełny zestaw usług w docker
compose (Whisper, Piper, Ollama, Assist Speaker ID) opisuje README
[Assist Routera](https://github.com/witold-gren/home-assistant-assist-router/blob/main/README.pl.md#usługi-w-docker-compose-bez-dodatków-ha).

### Integracja

Zainstaluj przez HACS (repozytorium niestandardowe, typ „Integracja”) i zrestartuj HA.
Potem: Ustawienia → Urządzenia i usługi → Dodaj → **Assist Speaker ID** (host, port Wyoming 10350,
port API 10351, token). Dodatek konfiguruje się sam przez odkrycie.
Zmiana hosta, portów albo tokenu API później: menu ⋮ integracji → **Skonfiguruj ponownie**
(osoby i agent w opcjach zostają bez zmian).

Opcje integracji (Konfiguruj) to menu z dwiema pozycjami:
- **Agent i osoby:**
  - przypisanie każdego zapisanego głosu do osoby HA (`person.*`);
  - agent, któremu „Agent z mówcą” przekazuje polecenia;
  - linia promptu (domyślnie w języku HA, np. `Pyta: {name}.`);
  - czy wykonywać polecenia jako użytkownik HA mówcy (`context.user_id`).
- **Progi rozpoznawania:** próg wspólny, margines, minimum mowy i próg każdej osoby.
  Zapisuje się w usłudze i działa od razu, bez restartu (opis liczb:
  [Jak czytać wyniki i progi](#jak-czytać-wyniki-i-progi)). Do tego **Prawdopodobny mówca od**
  (domyślnie 0.40, zapis w HA): zobacz [Prawdopodobny mówca](#prawdopodobny-mówca).

## Użycie

1. **Pipeline:** Ustawienia → Asystenci głosowi → pipeline → Zamiana mowy na tekst:
   **„Mowa na tekst z mówcą”**.
2. **Agent:**
   - **„Agent z mówcą”** z dowolnym agentem docelowym. Agent LLM dostaje informację
     o mówcy w prompcie systemowym.
   - **Albo własny agent, który czyta mówcę sam:**

     ```python
     try:
         from custom_components.speaker_id.api import get_speaker
     except ImportError:
         get_speaker = None
     speaker = get_speaker(hass) if get_speaker else None
     if speaker and speaker.speaker:
         ...  # speaker.speaker (id), .name, .person, .user_id, .score
     elif speaker and speaker.probable:
         ...  # not certain: .probable (id), .probable_name, .probable_person
     ```

     Tak robi [Assist Router](https://github.com/witold-gren/home-assistant-assist-router): pytanie
     w 1. osobie bez imienia („jakie **mam** zajęcia”) dotyczy danych mówcy. Do tego
     dochodzi pokój mówcy, określenia rodzinne („mój brat”) i odpowiedź na „Kto to mówi?”.
     Opis: [dokumentacja Assist Routera](https://witold-gren.github.io/home-assistant-assist-router/pl/).
   - **Nagrywanie wzorca w agencie:** `enrollment_reply(hass)` zwraca krótką odpowiedź
     („Nagrano 3 z 10. Kolejne zdanie.”) i informację, czy słuchać dalej, albo `None` dla
     zwykłej wypowiedzi. Agent, który to wywoła na początku, nie wykonuje poleceń podczas
     nagrywania i nie każe czekać na odpowiedź modelu. „Agent z mówcą” i Assist Router
     robią to same.
3. **Automatyzacje:** sensor „Ostatni mówca” (stan: imię albo `unknown`; atrybuty:
   `speaker`, `name`, `person`, `user_id`, `score`, `scores`, `speech_seconds`, `probable`,
   `probable_name`, `probable_person`, `probable_user_id`, `enrollment`) i
   zdarzenie `speaker_id_identified`. Treść wypowiedzi celowo nie trafia do historii.
   Automatyzacje ze zdaniami Assist HA uruchamia przed agentem, więc mówcy nie dostają
   wprost. Sensor zmienia się jednak w tym samym poleceniu, tuż przed nimi. Wynik sprzed
   kilku sekund to mówca tego zdania. Przykładowe makro
   (`/config/custom_templates/mowca.jinja`):

   ```jinja
   {% macro mowca(max_s=20) -%}
     {%- set s = states.sensor.speaker_id_last_speaker -%}
     {%- if s and (now() - s.last_updated).total_seconds() <= max_s -%}
       {{- s.attributes.speaker or '' -}}
     {%- endif -%}
   {%- endmacro %}
   ```

   W automatyzacji: `{% from 'mowca.jinja' import mowca %}{% if mowca() == 'anna' %}…`,
   np. „gdzie jest mój telefon” dzwoni wtedy na telefon mówcy. Identyfikatory encji są takie
   same w każdym języku HA: `sensor.speaker_id_last_speaker`, `stt.speaker_id_speech_to_text`,
   `conversation.speaker_id_agent` (id z wersji 0.1.x, zbudowane z polskiej nazwy, zmieniają się
   same; sprawdź potoki Assist). Po utworzeniu pliku uruchom akcję
   `homeassistant.reload_custom_templates`.

### Prawdopodobny mówca

Gdy głos nie osiągnie progu swojej osoby (albo jest za blisko drugiej), wynik to `unknown`,
ale najbardziej podobna osoba jest podawana jako **prawdopodobna**, jeśli podobieństwo wynosi co
najmniej **Prawdopodobny mówca od** (opcje → Progi rozpoznawania, domyślnie 0.40): atrybuty
`probable`, `probable_name`, `probable_person`, `probable_user_id`. Przydaje się tam, gdzie
zgadywanie jest w porządku i można je powiedzieć na głos - Assist Router odpowiada „Wydaje mi
się, że pyta Zosia. Dzisiaj masz…”. Prawdopodobny mówca nigdy nie wykonuje poleceń jako
użytkownik HA tej osoby (`context.user_id` bez zmian) - `Context` w HA nie ma miejsca na
dodatkowe pola, więc automatyzacje czytają prawdopodobną osobę z sensora:
`state_attr('sensor.speaker_id_last_speaker', 'probable_person')`.

## Ogłoszenia (intercom): Twoje nagranie zamiast syntezy

„OK Nabu, **ogłoszenie**, dzieci proszę przyjść na obiad.” Pozostałe głośniki odtwarzają
**Twoje nagranie**, dokładnie to, co powiedziałeś po słowie wybudzającym, bez cięcia i bez
zmian, zamiast czytać tekst głosem Pipera.

- Nagranie pochodzi z usługi: to ostatnia wypowiedź, trzymana tylko w pamięci RAM.
- HA podaje je głośnikom pod jednorazowym, podpisanym adresem
  `/api/speaker_id/announcement/<token>`, ważnym 10 minut. Nic nie jest zapisywane na
  dysku.
- **Akcja** `speaker_id.announce_last`:

  | Pole | Domyślnie | Znaczenie |
  |---|---|---|
  | `satellites` | wszystkie satelity z obsługą ogłoszeń | gdzie zagrać |
  | `exclude` | brak | pominięty satelita, zwykle ten, który nagrał |
  | `media_players` | brak | dodatkowe odtwarzacze, np. HomePody przez Music Assistant (`play_media` z `announce: true`) |
  | `preannounce` | `true` | krótki sygnał przed nagraniem |
  | `prefer_players` | `false` | jeden głośnik w pokoju: dostępny odtwarzacz (np. HomePod) zamiast satelity; `off` liczy się jako niedostępny (rozłączony HomePod w Apple TV); pokój, z którego pada ogłoszenie, nic nie dostaje |
  | `max_age` | 60 s | starsza ostatnia wypowiedź nie jest ogłaszana |

- **Dla agenta konwersacji:** `async_announce_last(hass, exclude=…, media_players=…)` z
  `custom_components.speaker_id.api`. Zwraca listę encji albo `None`, gdy nie ma świeżego
  nagrania (np. tekst wpisany ręcznie). Assist Router rozpoznaje „ogłoszenie”,
  „komunikat”, „do wszystkich”, „intercom” na początku zdania i woła tę funkcję. Głośnik,
  który nagrał, odpowiada tylko „Ogłoszenie wysłane.”.
- **Ważne:** w potoku wyłącz „Preferuj obsługę poleceń lokalnie”. Inaczej wbudowana
  intencja rozgłaszania HA złapie „Ogłoszenie, …” przed agentem i przeczyta tekst
  syntezą. Z tego samego powodu agent nie zdąży wtedy odpowiedzieć krótko przy
  nagrywaniu wzorca.

## Wzorce głosu i kalibracja

| Akcja | Co robi |
|---|---|
| `speaker_id.start_enrollment` | `speaker: anna`, `name: Anna`, `count: 10`, opcjonalnie `satellite: assist_satellite.…`: kolejne 10 wypowiedzi z co najmniej 1,5 s mowy to próbki Anny. **Z `satellite` głośnik prowadzi nagrywanie sam**, bez słowa wybudzającego. Prosi o pierwsze zdanie, a po każdym mówi raz „Nagrano 3 z 10. Kolejne zdanie.” i od razu słucha dalej, bez wykonywania polecenia. Prosi o dłuższe, gdy zdanie było za krótkie, i ponawia pytanie po ciszy albo urwanej rozmowie. Kończy po ostatniej próbce („Gotowe…”) albo po 3 próbach bez nowej. Bez `satellite` każde zdanie zaczyna się od słowa wybudzającego. Postęp widać w atrybucie `enrollment` sensora. **Kolejna seria dochodzi do zapisanych próbek** i liczy wszystkie: trzecia seria po 10 mówi „Nagrano 24 z 30”. |
| `speaker_id.add_last_sample` | ostatnia wypowiedź (najwyżej sprzed 10 min) jako próbka osoby |
| `speaker_id.calibrate` | dla każdej osoby porównuje jej próbki z próbkami pozostałych i proponuje próg; z `apply: true` zapisuje progi |
| `speaker_id.reset_speaker` | usuwa wszystkie próbki i nagrania osoby (oraz jej własny próg), osoba zostaje - nagrywanie od nowa |
| `speaker_id.rebuild_voiceprints` | liczy każdą próbkę od nowa z zapisanego nagrania (np. po zmianie modelu); próbki bez nagrania zostają bez zmian |
| `speaker_id.set_speaker` / `delete_speaker` / `list_speakers` | nazwa i własny próg / usunięcie wzorca razem z nagraniami / lista (próbki, nagrania) |

**Wskazówki:**
- Nagrywaj przez te satelity i z tej odległości, z której domownik zwykle mówi. Mikrofon
  i pomieszczenie zmieniają głos bardziej niż słowa.
- Używaj zwykłych poleceń po kilka słów; najlepiej 10–15 na osobę.
- Kalibracja pokaże `separable: false`, gdy próbki dwóch osób się przenikają. To częste
  u rodzeństwa: dograj więcej próbek albo podnieś `margin`.
- Głosy dzieci zmieniają się z wiekiem, więc co kilka miesięcy dograj nowe próbki.
- **Dogrywaj próbki seriami** (np. 3 × 10 przez kilka dni, z różnych satelit): wzorzec to
  średnia wszystkich próbek, więc z każdą serią jest dokładniejszy. Po każdej serii uruchom
  `calibrate`. Każda próbka zachowuje swoje nagranie w usłudze (`/data/recordings/<osoba>/`,
  ok. 0,2 MB na próbkę) aż do `reset_speaker` albo `delete_speaker`, więc wzorce można później
  przeliczyć od nowa (`rebuild_voiceprints`). Próbki sprzed wersji 0.2.0 nie mają nagrań.

API HTTP usługi (port 10351, opis w `speaker_id/wyoming_speaker_id/http_api.py`) obsługuje
też wgrywanie nagrań WAV (`POST /api/speakers/{id}/samples`) i test (`POST /api/identify`).

## Jak czytać wyniki i progi

### Podobieństwo: im wyżej, tym bardziej „to ta osoba”

Model zamienia kilka sekund mowy na **wektor**, czyli 192 liczby opisujące barwę głosu:
wysokość, rezonans, sposób artykulacji. Wektor nie zależy od treści zdania. **Wzorzec osoby**
to średnia wektorów z jej próbek.

Każda wypowiedź jest porównywana z wzorcem każdej osoby. Wynikiem jest **podobieństwo**
(cosinus kąta między wektorami), zwykle od 0 do 1:

| Podobieństwo | Co zwykle oznacza (model CAM++, mikrofon satelity) |
|---|---|
| 0,75–0,95 | ta sama osoba, dobre warunki |
| 0,55–0,75 | ta sama osoba, ale krótko, z daleka, w hałasie albo innym tonem |
| 0,35–0,55 | podobny głos, często rodzeństwo albo rodzic i dziecko tej samej płci |
| 0–0,35 | inna osoba |

To **nie jest „unikatowość” głosu**. Wysoki wynik oznacza, że *ta wypowiedź* jest bardzo
podobna do *tego wzorca*. „Kto to mówi?” podaje tę liczbę w procentach.

### Próg i margines: poprzeczka do przeskoczenia

Najlepiej pasująca osoba zostaje rozpoznana tylko wtedy, gdy spełnia oba warunki:

1. **podobieństwo ≥ próg tej osoby**, czyli jej własny próg albo wspólny, gdy własnego nie
   ma;
2. **podobieństwo − podobieństwo drugiej osoby ≥ margines.** Przy dwóch prawie równych
   wynikach odpowiedź brzmi „nie jestem pewien”, a nie zgadywanie.

Jeśli któryś warunek nie jest spełniony, mówca jest „nieznany”, a Assist działa jak bez
rozpoznawania.

- **Wyższy próg** jest ostrzejszy: mniej pomyłek, ale częściej „nieznany”.
- **Niższy próg** jest łagodniejszy: rzadziej „nieznany”, ale łatwiej o pomyłkę.

Dla podobnych głosów potrzebny jest **wyższy** próg albo większy margines. Nie dlatego, że
ich głos jest „mniej unikatowy” w sensie słabszego wzorca, tylko dlatego, że inne głosy
zbliżają się do ich wzorca. Poprzeczka musi być więc wyżej, żeby rodzeństwo jej nie
przeskoczyło.

| Parametr | Domyślnie | Gdzie |
|---|---|---|
| Próg wspólny | 0,50 | opcje integracji → Progi rozpoznawania (albo `SPEAKER_ID_THRESHOLD` / opcja dodatku jako wartość startowa) |
| Próg osoby | brak = wspólny | opcje integracji → Progi rozpoznawania, akcja `speaker_id.set_speaker`, kalibracja z `apply: true` |
| Margines | 0,05 | opcje integracji (albo `SPEAKER_ID_MARGIN`) |
| Minimum mowy | 1,0 s | opcje integracji (albo `SPEAKER_ID_MIN_SPEECH`); krótsza wypowiedź = zawsze „nieznany” |

Wartości ustawione w opcjach integracji są zapisane w `speakers.json` usługi i mają
pierwszeństwo przed zmiennymi środowiska i opcjami dodatku. Te są tylko wartością startową
przy pierwszym uruchomieniu. Zmiana działa od następnej wypowiedzi, bez restartu.

### Raport kalibracji

`speaker_id.calibrate` liczy dla każdej osoby:

| Pole | Znaczenie |
|---|---|
| `samples` | liczba próbek |
| `own_min` | **najgorsza** własna próbka: podobieństwo do wzorca z *pozostałych* próbek tej osoby (próbka nie porównuje się sama ze sobą) |
| `own_p10` | 10. percentyl własnych próbek: 9 na 10 próbek jest co najmniej tak podobnych |
| `own_mean` | średnie podobieństwo własnych próbek |
| `others_max` | **najbardziej podobna cudza** próbka, czyli najwyższe podobieństwo próbki innej osoby do tego wzorca |
| `separable` | `true`, gdy `own_min > others_max`: żadna cudza próbka nie zbliżyła się do najgorszej własnej |
| `suggested_threshold` | środek między `own_p10` a `others_max` (z zakresu 0,2–0,9) |

**Przykład (Tomek):**

```yaml
samples: 10
own_min: 0.709      # najgorsza własna próbka jest 71% podobna do wzorca
own_p10: 0.778      # 9 z 10 własnych próbek ma co najmniej 78%
own_mean: 0.803     # średnio 80%
others_max: 0.305   # najbardziej podobna cudza próbka ma tylko 31%
separable: true     # 0.709 > 0.305 - głosy wyraźnie rozdzielone
suggested_threshold: 0.541   # (0.778 + 0.305) / 2
```

Między najgorszą własną próbką (71%) a najbliższą cudzą (31%) jest **40 punktów przerwy**.
Głos jest bardzo łatwy do odróżnienia. Każdy próg w przedziale ~0,35–0,70 działa;
proponowane 0,54 leży pośrodku, z zapasem w obie strony. Twoje obserwacje (powyżej ~60% to
na pewno ta osoba) się z tym zgadzają: 0,60 też jest bezpieczne.

U dziecka kalibracja może pokazać na przykład `own_p10: 0.74`, `others_max: 0.68`
(rodzeństwo). Wtedy `suggested_threshold: 0.71`, a przerwa ma tylko 6 punktów. Próg musi
być wysoko, a i tak warto mieć margines.

### Jak kalibrować podobne głosy (np. rodzeństwo)

1. **Więcej i lepszych próbek:** 15–20 na osobę, nagranych przez satelitę, z którego ta osoba
   zwykle mówi, z typowej odległości, w zwykłym tonie. Krzyk, szept i śmiech zaniżają wynik.
2. **Kalibracja po nagraniu wszystkich osób:** `others_max` liczy się tylko z osób, które
   mają już wzorce.
3. **Sprawdzenie `separable`:** przy `false` jakaś cudza próbka przeskoczyła najgorszą
   własną. Często jedna nieudana próbka (ktoś się wtrącił, był hałas) zaniża `own_min`.
   Dograj próbki albo nagraj tę osobę od nowa (`delete_speaker` i nagrywanie).
4. **Próg między `others_max` a `own_p10`:** bliżej `own_p10` oznacza mniej pomyłek, ale
   częściej „nieznany”. Bliżej `others_max` działa odwrotnie.
5. **Margines 0,08–0,15 przy rodzeństwie:** zamiast zgadywać, gdy dwa wyniki są blisko,
   asystent powie „nie jestem pewien”.
6. **Test „Kto to mówi?”** na kilku dłuższych zdaniach każdej osoby. Patrz na podobieństwo
   i na wynik „następnej osoby”: przerwa między nimi to Twój faktyczny zapas.

Kalibracja z `apply: true` nadpisuje progi osób. Ręczne poprawki rób po niej albo
nie stosuj `apply`.

## Ograniczenia

- **Krótkie polecenia:** „Tak”, „Stop” i podobne (poniżej 1 s mowy) dają „nieznany”.
- **Słowo wybudzające nie jest używane:** satelita wykrywa je sam, więc do zamiany mowy na
  tekst trafia tylko polecenie. Rozpoznanie nie zależy od treści.
- **To wygoda, nie zabezpieczenie:** głos można nagrać i odtworzyć. Nie używaj mówcy do
  otwierania zamków ani wyłączania alarmu.

## Prywatność

Wzorzec głosu to dane biometryczne (RODO, art. 9):
- wszystko działa lokalnie;
- usługa zapisuje wektory (`/data/speakers.json`) i nagrania próbek głosu
  (`/data/recordings/`), nigdy poza Twoją sieć;
- ostatnia wypowiedź jest trzymana wyłącznie w pamięci, najwyżej 10 minut (do
  `add_last_sample`);
- domownicy powinni wiedzieć o rozpoznawaniu i się na nie zgodzić;
- `delete_speaker` usuwa wzorzec osoby razem z nagraniami, `reset_speaker` tylko próbki i
  nagrania.

## Rozwój

```sh
python3.13 -m venv .venv-service && .venv-service/bin/pip install -r speaker_id/requirements.txt pytest pytest-asyncio ruff
python3.13 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
scripts/test.sh     # testy usługi i integracji, ruff
./deploy.sh         # integracja do /Volumes/config/custom_components (udział Samba)
```

Licencja: Apache-2.0. Model 3D-Speaker CAM++: Apache-2.0 (modelscope/3D-Speaker), przez
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).
