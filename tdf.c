/*
 * tdf - Tour de France 2026 results from the official racecenter API
 *
 * Usage:
 *   tdf                  Show Stage 1 results (or current/latest stage)
 *   tdf 3                Show Stage 3 results
 *   tdf 3 --checkpoints  Show all checkpoints for stage 3
 *   tdf --gc             Show general classification after latest stage
 *   tdf --teams          List all teams
 *   tdf --stages         List all stages with routes
 *   tdf --riders         List all riders (184)
 *   tdf 2 --top 5        Show top 5 for stage 2
 *
 * Build: gcc -O2 -o tdf tdf.c -lcurl
 *   (or with static curl: gcc -O2 -o tdf tdf.c -lcurl -lz -lssl -lcrypto -lpthread -ldl -static)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <ctype.h>
#include <math.h>
#include <curl/curl.h>
#include <unistd.h>

#define BASE_URL "https://racecenter.letour.fr/api/"
#define YEAR 2026
#define MAX_DOWNLOAD_SIZE (4 * 1024 * 1024)  /* 4MB - largest API response is ~160KB */
#define MAX_STAGES 21
#define MAX_RIDERS 256
#define MAX_TEAMS 32
#define MAX_RANKINGS 256
#define MAX_CHECKPOINTS 16

/* ----- Dynamic string for curl downloads ----- */
typedef struct {
    char *data;
    size_t size;
    size_t capacity;
} DynamicString;

static void ds_init(DynamicString *ds) {
    ds->capacity = 65536;
    ds->data = malloc(ds->capacity);
    ds->data[0] = '\0';
    ds->size = 0;
}

static void ds_free(DynamicString *ds) {
    free(ds->data);
    ds->data = NULL;
}

static size_t write_cb(void *ptr, size_t size, size_t nmemb, void *userdata) {
    DynamicString *ds = (DynamicString *)userdata;
    size_t total = size * nmemb;
    if (ds->size + total + 1 > ds->capacity) {
        while (ds->size + total + 1 > ds->capacity)
            ds->capacity *= 2;
        ds->data = realloc(ds->data, ds->capacity);
    }
    memcpy(ds->data + ds->size, ptr, total);
    ds->size += total;
    ds->data[ds->size] = '\0';
    return total;
}

static char *fetch_url(const char *url) {
    CURL *curl = curl_easy_init();
    if (!curl) return NULL;

    DynamicString ds;
    ds_init(&ds);

    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &ds);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "Mozilla/5.0");
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 20L);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);

    CURLcode res = curl_easy_perform(curl);
    long http_code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);

    curl_easy_cleanup(curl);

    if (res != CURLE_OK || http_code != 200 || ds.size == 0) {
        ds_free(&ds);
        return NULL;
    }

    return ds.data; /* caller frees */
}

/* ----- Minimal JSON parser (enough for these APIs) ----- */
typedef enum {
    JSON_NULL, JSON_BOOL, JSON_NUMBER, JSON_STRING, JSON_ARRAY, JSON_OBJECT
} JsonType;

typedef struct JsonValue {
    JsonType type;
    /* For number */
    double num;
    /* For string/bool */
    char *str;          /* decoded string value */
    bool boolean;
    /* For array/object */
    struct JsonPair {
        char *key;              /* NULL for array elements */
        struct JsonValue *value;
    } *pairs;
    int count;
    int capacity;
} JsonValue;

static JsonValue *parse_json(const char **p);
static void json_free(JsonValue *v);

static void skip_ws(const char **p) {
    while (**p && (**p == ' ' || **p == '\t' || **p == '\n' || **p == '\r'))
        (*p)++;
}

static JsonValue *new_json(JsonType type) {
    JsonValue *v = calloc(1, sizeof(JsonValue));
    v->type = type;
    v->pairs = NULL;
    v->count = 0;
    v->capacity = 0;
    return v;
}

static void json_add_pair(JsonValue *v, char *key, JsonValue *val) {
    if (v->count >= v->capacity) {
        v->capacity = v->capacity ? v->capacity * 2 : 8;
        v->pairs = realloc(v->pairs, v->capacity * sizeof(v->pairs[0]));
    }
    v->pairs[v->count].key = key;
    v->pairs[v->count].value = val;
    v->count++;
}

/* Parse a JSON string (without quotes) - handles escape sequences */
static char *parse_string_val(const char **p) {
    if (**p != '"') return NULL;
    (*p)++;
    char *buf = malloc(4096);
    int len = 0;
    while (**p && **p != '"') {
        if (**p == '\\') {
            (*p)++;
            switch (**p) {
                case 'n': buf[len++] = '\n'; break;
                case 't': buf[len++] = '\t'; break;
                case 'r': buf[len++] = '\r'; break;
                case '"': buf[len++] = '"'; break;
                case '\\': buf[len++] = '\\'; break;
                case '/': buf[len++] = '/'; break;
                case 'u': {
                    /* Decode \uXXXX (basic BMP, no surrogates) */
                    char hex[5] = {0};
                    for (int i = 0; i < 4; i++) { (*p)++; hex[i] = **p; }
                    unsigned int cp = (unsigned int)strtol(hex, NULL, 16);
                    if (cp < 0x80) {
                        buf[len++] = (char)cp;
                    } else if (cp < 0x800) {
                        buf[len++] = (char)(0xC0 | (cp >> 6));
                        buf[len++] = (char)(0x80 | (cp & 0x3F));
                    } else {
                        buf[len++] = (char)(0xE0 | (cp >> 12));
                        buf[len++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                        buf[len++] = (char)(0x80 | (cp & 0x3F));
                    }
                    break;
                }
                default: buf[len++] = **p; break;
            }
            (*p)++;
        } else {
            buf[len++] = **p;
            (*p)++;
        }
    }
    if (**p == '"') (*p)++;
    buf[len] = '\0';
    return buf;
}

static JsonValue *parse_json(const char **p) {
    skip_ws(p);
    if (!**p) return NULL;

    char c = **p;

    if (c == '"') {
        char *s = parse_string_val(p);
        JsonValue *v = new_json(JSON_STRING);
        v->str = s;
        return v;
    }

    if (c == '{') {
        (*p)++;
        JsonValue *v = new_json(JSON_OBJECT);
        skip_ws(p);
        if (**p == '}') { (*p)++; return v; }
        while (**p) {
            skip_ws(p);
            char *key = parse_string_val(p);
            skip_ws(p);
            if (**p == ':') (*p)++;
            JsonValue *val = parse_json(p);
            json_add_pair(v, key, val);
            skip_ws(p);
            if (**p == ',') { (*p)++; continue; }
            if (**p == '}') { (*p)++; break; }
            break;
        }
        return v;
    }

    if (c == '[') {
        (*p)++;
        JsonValue *v = new_json(JSON_ARRAY);
        skip_ws(p);
        if (**p == ']') { (*p)++; return v; }
        while (**p) {
            JsonValue *val = parse_json(p);
            json_add_pair(v, NULL, val);
            skip_ws(p);
            if (**p == ',') { (*p)++; continue; }
            if (**p == ']') { (*p)++; break; }
            break;
        }
        return v;
    }

    if (c == 't' && strncmp(*p, "true", 4) == 0) {
        *p += 4;
        JsonValue *v = new_json(JSON_BOOL);
        v->boolean = true;
        return v;
    }

    if (c == 'f' && strncmp(*p, "false", 5) == 0) {
        *p += 5;
        JsonValue *v = new_json(JSON_BOOL);
        v->boolean = false;
        return v;
    }

    if (c == 'n' && strncmp(*p, "null", 4) == 0) {
        *p += 4;
        return new_json(JSON_NULL);
    }

    /* Number */
    {
        const char *start = *p;
        if (c == '-') (*p)++;
        while (**p && (isdigit((unsigned char)**p) || **p == '.' || **p == 'e' || **p == 'E' || **p == '+' || **p == '-'))
            (*p)++;
        int len = (int)(*p - start);
        char *numbuf = malloc(len + 1);
        memcpy(numbuf, start, len);
        numbuf[len] = '\0';
        JsonValue *v = new_json(JSON_NUMBER);
        v->num = strtod(numbuf, NULL);
        free(numbuf);
        return v;
    }
}

static void json_free(JsonValue *v) {
    if (!v) return;
    if (v->str) free(v->str);
    for (int i = 0; i < v->count; i++)
        json_free(v->pairs[i].value);
    if (v->pairs) free(v->pairs);
    free(v);
}

/* ----- JSON helpers ----- */
static JsonValue *json_get(JsonValue *obj, const char *key) {
    if (!obj || obj->type != JSON_OBJECT) return NULL;
    for (int i = 0; i < obj->count; i++) {
        if (obj->pairs[i].key && strcmp(obj->pairs[i].key, key) == 0)
            return obj->pairs[i].value;
    }
    return NULL;
}

static const char *json_str(JsonValue *obj, const char *key) {
    JsonValue *v = json_get(obj, key);
    if (v && v->type == JSON_STRING) return v->str;
    return "";
}

static double json_num(JsonValue *obj, const char *key) {
    JsonValue *v = json_get(obj, key);
    if (v && v->type == JSON_NUMBER) return v->num;
    return 0;
}

static int json_int(JsonValue *obj, const char *key) {
    return (int)json_num(obj, key);
}

static bool json_has(JsonValue *obj, const char *key) {
    return json_get(obj, key) != NULL;
}

/* ----- Data structures ----- */
typedef struct {
    int bib;
    char firstname[64];
    char lastname[64];
    char nationality[8];
    char team_id[128];
    char team_name[64];
    char team_code[8];
} Rider;

typedef struct {
    int position;
    int bib;
    long time_ms;
    long gap_ms;
    int penalty;
    int bonus;
} RankEntry;

typedef struct {
    int checkpoint;
    double length;
    char type[8];
    int n_rankings;
    RankEntry rankings[MAX_RANKINGS];
} Checkpoint;

typedef struct {
    int stage_num;
    char departure[64];
    char arrival[64];
    char type[8];
    double length;
    char date[16];
    char start_time[16];
    char end_time[16];
} Stage;

static Rider g_riders[MAX_RIDERS];
static int g_n_riders = 0;
static bool g_riders_loaded = false;

static void load_riders_and_teams(void) {
    if (g_riders_loaded) return;

    char url[256];
    snprintf(url, sizeof(url), BASE_URL "allCompetitors-%d", YEAR);
    char *raw = fetch_url(url);
    if (!raw) {
        fprintf(stderr, "Error: could not fetch rider data from API\n");
        return;
    }

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);

    if (!root || root->type != JSON_ARRAY) {
        if (root) json_free(root);
        return;
    }

    /* First pass: build team lookup from entries without bib */
    /* The API returns rider and team objects mixed together */
    struct { char id[128]; char name[64]; char code[8]; } teams[MAX_TEAMS];
    int n_teams = 0;

    for (int i = 0; i < root->count && n_teams < MAX_TEAMS; i++) {
        JsonValue *item = root->pairs[i].value;
        if (!json_has(item, "bib")) {
            const char *id = json_str(item, "_id");
            const char *name = json_str(item, "name");
            const char *code = json_str(item, "code");
            if (id[0] && name[0]) {
                strncpy(teams[n_teams].id, id, sizeof(teams[n_teams].id) - 1);
                strncpy(teams[n_teams].name, name, sizeof(teams[n_teams].name) - 1);
                strncpy(teams[n_teams].code, code, sizeof(teams[n_teams].code) - 1);
                n_teams++;
            }
        }
    }

    /* Also fetch the separate team endpoint for better team data */
    snprintf(url, sizeof(url), BASE_URL "team-%d", YEAR);
    char *team_raw = fetch_url(url);
    if (team_raw) {
        const char *tp = team_raw;
        JsonValue *troot = parse_json(&tp);
        free(team_raw);
        if (troot && troot->type == JSON_ARRAY) {
            n_teams = 0;
            for (int i = 0; i < troot->count && n_teams < MAX_TEAMS; i++) {
                JsonValue *item = troot->pairs[i].value;
                const char *id = json_str(item, "_id");
                const char *name = json_str(item, "name");
                const char *code = json_str(item, "code");
                if (id[0]) {
                    strncpy(teams[n_teams].id, id, sizeof(teams[n_teams].id) - 1);
                    strncpy(teams[n_teams].name, name, sizeof(teams[n_teams].name) - 1);
                    strncpy(teams[n_teams].code, code, sizeof(teams[n_teams].code) - 1);
                    n_teams++;
                }
            }
        }
        if (troot) json_free(troot);
    }

    /* Second pass: extract riders */
    for (int i = 0; i < root->count && g_n_riders < MAX_RIDERS; i++) {
        JsonValue *item = root->pairs[i].value;
        if (!json_has(item, "bib")) continue;

        Rider *r = &g_riders[g_n_riders++];
        memset(r, 0, sizeof(Rider));
        r->bib = json_int(item, "bib");
        const char *fn = json_str(item, "firstname");
        const char *ln = json_str(item, "lastname");
        const char *nat = json_str(item, "nationality");
        const char *tid = json_str(item, "$team");
        strncpy(r->firstname, fn, sizeof(r->firstname) - 1);
        strncpy(r->lastname, ln, sizeof(r->lastname) - 1);
        strncpy(r->nationality, nat, sizeof(r->nationality) - 1);
        strncpy(r->team_id, tid, sizeof(r->team_id) - 1);

        /* $team is "team-2026:HASH" but team _id is just "HASH" - strip prefix */
        char team_hash[128];
        const char *colon = strchr(r->team_id, ':');
        if (colon) {
            strncpy(team_hash, colon + 1, sizeof(team_hash) - 1);
            team_hash[sizeof(team_hash) - 1] = '\0';
        } else {
            strncpy(team_hash, r->team_id, sizeof(team_hash) - 1);
        }

        /* Find team name by hash */
        for (int t = 0; t < n_teams; t++) {
            if (strcmp(teams[t].id, team_hash) == 0) {
                strncpy(r->team_name, teams[t].name, sizeof(r->team_name) - 1);
                strncpy(r->team_code, teams[t].code, sizeof(r->team_code) - 1);
                break;
            }
        }
    }

    json_free(root);
    g_riders_loaded = true;
}

static Rider *find_rider(int bib) {
    for (int i = 0; i < g_n_riders; i++) {
        if (g_riders[i].bib == bib)
            return &g_riders[i];
    }
    return NULL;
}

/* ----- Formatting helpers ----- */
static void format_time(long ms, char *out, size_t outsz) {
    long s = ms / 1000;
    int ms_part = (int)(ms % 1000);
    snprintf(out, outsz, "%02ld:%02ld:%02d.%03d", s/3600, (s/60)%60, (int)(s%60), ms_part);
}

static void format_gap(long ms, char *out, size_t outsz) {
    if (ms == 0) {
        out[0] = '\0';
        return;
    }
    long s = ms / 1000;
    int ms_part = (int)(ms % 1000);
    if (s < 60) {
        snprintf(out, outsz, "+%ld.%03ds", s, ms_part);
    } else {
        long m = s / 60;
        int sec = (int)(s % 60);
        snprintf(out, outsz, "+%ldm%02d", m, sec);
    }
}

/* ----- Commands ----- */

static int find_latest_stage(void) {
    char url[256];
    snprintf(url, sizeof(url), BASE_URL "stage-%d", YEAR);
    char *raw = fetch_url(url);
    if (!raw) return 1;

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);
    if (!root || root->type != JSON_ARRAY) {
        if (root) json_free(root);
        return 1;
    }

    int latest = 1;
    for (int i = 0; i < root->count; i++) {
        JsonValue *item = root->pairs[i].value;
        int stage = json_int(item, "stage");
        if (stage > latest) latest = stage;
    }
    json_free(root);
    return latest;
}

static void cmd_stages(void) {
    char url[256];
    snprintf(url, sizeof(url), BASE_URL "stage-%d", YEAR);
    char *raw = fetch_url(url);
    if (!raw) {
        fprintf(stderr, "Error: could not fetch stage data\n");
        return;
    }

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);
    if (!root || root->type != JSON_ARRAY) {
        if (root) json_free(root);
        return;
    }

    /* Sort stages by stage number */
    int indices[MAX_STAGES];
    int n = root->count < MAX_STAGES ? root->count : MAX_STAGES;
    for (int i = 0; i < n; i++) indices[i] = i;
    for (int i = 0; i < n - 1; i++) {
        for (int j = i + 1; j < n; j++) {
            int si = json_int(root->pairs[indices[i]].value, "stage");
            int sj = json_int(root->pairs[indices[j]].value, "stage");
            if (sj < si) { int tmp = indices[i]; indices[i] = indices[j]; indices[j] = tmp; }
        }
    }

    printf("Tour de France %d - %d Stages\n", YEAR, n);
    printf("%-4s %-12s %-30s %-30s %-6s %-8s\n", "Stg", "Date", "From", "To", "KM", "Type");
    printf("------------------------------------------------------------------------------------------------\n");

    for (int idx = 0; idx < n; idx++) {
        JsonValue *item = root->pairs[indices[idx]].value;
        int stage = json_int(item, "stage");
        const char *date = json_str(item, "date");
        const char *dep = json_str(json_get(item, "departureCity"), "label");
        const char *arr = json_str(json_get(item, "arrivalCity"), "label");
        double len = json_num(item, "length");
        const char *type = json_str(item, "type");

        /* Truncate date to just YYYY-MM-DD */
        char date_short[11] = {0};
        strncpy(date_short, date, 10);

        /* Decode stage type (ASO uses codes: EQU=TTT, IND=ITT, VAL=road, PLN=flat, PAS=mountain pass) */
        const char *type_str = "Road";
        if (strcasecmp(type, "EQU") == 0) type_str = "TTT";
        else if (strcasecmp(type, "IND") == 0) type_str = "ITT";
        else if (strcasecmp(type, "PAS") == 0) type_str = "Mountain";
        else if (strcasecmp(type, "PLN") == 0) type_str = "Flat";

        printf("%-4d %-12s %-30s %-30s %-6.0f %-8s\n", stage, date_short, dep, arr, len, type_str);
    }
    json_free(root);
}

static void cmd_teams(void) {
    char url[256];
    snprintf(url, sizeof(url), BASE_URL "team-%d", YEAR);
    char *raw = fetch_url(url);
    if (!raw) {
        fprintf(stderr, "Error: could not fetch team data\n");
        return;
    }

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);
    if (!root || root->type != JSON_ARRAY) {
        if (root) json_free(root);
        return;
    }

    printf("Tour de France %d - Teams (%d)\n", YEAR, root->count);
    printf("%-4s %-40s %-6s %-12s\n", "#", "Team Name", "Code", "Country");
    printf("--------------------------------------------------------\n");

    for (int i = 0; i < root->count; i++) {
        JsonValue *item = root->pairs[i].value;
        const char *name = json_str(item, "name");
        const char *code = json_str(item, "code");
        const char *nat = json_str(item, "nationality");
        printf("%-4d %-40s %-6s %-12s\n", i+1, name, code, nat);
    }
    json_free(root);
}

static void cmd_riders(void) {
    load_riders_and_teams();
    if (g_n_riders == 0) {
        fprintf(stderr, "Error: could not load rider data\n");
        return;
    }

    printf("Tour de France %d - Riders (%d)\n", YEAR, g_n_riders);
    printf("%-4s %-26s %-4s %-6s %-40s\n", "Bib", "Name", "Nat", "Code", "Team");
    printf("-----------------------------------------------------------------------------\n");

    for (int i = 0; i < g_n_riders; i++) {
        Rider *r = &g_riders[i];
        char fullname[128];
        snprintf(fullname, sizeof(fullname), "%s %s", r->firstname, r->lastname);
        printf("%-4d %-26s %-4s %-6s %-40s\n", r->bib, fullname, r->nationality, r->team_code, r->team_name);
    }
}

static int fetch_stage_rankings(int stage, Checkpoint *cps, int *n_cps, const char *classification) {
    char url[256];
    snprintf(url, sizeof(url), BASE_URL "%s-%d-%d", classification, YEAR, stage);
    char *raw = fetch_url(url);
    if (!raw) return -1;

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);
    if (!root || root->type != JSON_ARRAY) {
        if (root) json_free(root);
        return -1;
    }

    *n_cps = 0;
    for (int i = 0; i < root->count && *n_cps < MAX_CHECKPOINTS; i++) {
        JsonValue *item = root->pairs[i].value;
        if (!json_has(item, "rankings")) continue;

        Checkpoint *cp = &cps[(*n_cps)++];
        memset(cp, 0, sizeof(Checkpoint));
        cp->checkpoint = json_int(item, "checkpoint");
        cp->length = json_num(item, "length");
        const char *t = json_str(item, "type");
        strncpy(cp->type, t, sizeof(cp->type) - 1);

        JsonValue *ranks = json_get(item, "rankings");
        if (ranks && ranks->type == JSON_ARRAY) {
            cp->n_rankings = 0;
            for (int j = 0; j < ranks->count && cp->n_rankings < MAX_RANKINGS; j++) {
                JsonValue *r = ranks->pairs[j].value;
                cp->rankings[cp->n_rankings].position = json_int(r, "position");
                cp->rankings[cp->n_rankings].bib = json_int(r, "bib");
                cp->rankings[cp->n_rankings].time_ms = (long)json_num(r, "absolute");
                cp->rankings[cp->n_rankings].gap_ms = (long)json_num(r, "relative");
                cp->rankings[cp->n_rankings].penalty = json_int(r, "penality");
                cp->rankings[cp->n_rankings].bonus = json_int(r, "bonus");
                cp->n_rankings++;
            }
        }
    }

    json_free(root);
    return 0;
}

static void print_stage_result(int stage, int top_n, bool show_checkpoints) {
    load_riders_and_teams();

    /* For TTT/ITT stages, use rankingTypeTrial; for road stages, use rankingType */
    /* We try rankingTypeTrial first (works for TTT), fall back to rankingType */
    Checkpoint cps[MAX_CHECKPOINTS];
    int n_cps = 0;

    /* Determine stage type from stage list */
    bool is_timetrial = false;
    {
        char url[256];
        snprintf(url, sizeof(url), BASE_URL "stage-%d", YEAR);
        char *raw = fetch_url(url);
        if (raw) {
            const char *p = raw;
            JsonValue *root = parse_json(&p);
            free(raw);
            if (root && root->type == JSON_ARRAY) {
                for (int i = 0; i < root->count; i++) {
                    JsonValue *item = root->pairs[i].value;
                    if (json_int(item, "stage") == stage) {
                        const char *type = json_str(item, "type");
                        if (strcasecmp(type, "EQU") == 0 || strcasecmp(type, "IND") == 0) {
                            is_timetrial = true;
                        }
                        break;
                    }
                }
            }
            if (root) json_free(root);
        }
    }

    const char *classification = is_timetrial ? "rankingTypeTrial" : "rankingType";
    int rc = fetch_stage_rankings(stage, cps, &n_cps, classification);
    if (rc != 0) {
        /* Fallback: try the other classification */
        classification = is_timetrial ? "rankingType" : "rankingTypeTrial";
        rc = fetch_stage_rankings(stage, cps, &n_cps, classification);
    }
    if (rc != 0) {
        fprintf(stderr, "Error: could not fetch results for stage %d. The stage may not have finished yet.\n", stage);
        return;
    }

    if (n_cps == 0) {
        fprintf(stderr, "Error: no ranking data available for stage %d.\n", stage);
        return;
    }

    /* Find finish - highest length checkpoint */
    int finish_idx = 0;
    for (int i = 1; i < n_cps; i++) {
        if (cps[i].length > cps[finish_idx].length)
            finish_idx = i;
    }

    Checkpoint *finish = &cps[finish_idx];

    /* Get stage info for header */
    char header[128] = {0};
    {
        char url[256];
        snprintf(url, sizeof(url), BASE_URL "stage-%d", YEAR);
        char *raw = fetch_url(url);
        if (raw) {
            const char *p = raw;
            JsonValue *root = parse_json(&p);
            free(raw);
            if (root && root->type == JSON_ARRAY) {
                for (int i = 0; i < root->count; i++) {
                    JsonValue *item = root->pairs[i].value;
                    if (json_int(item, "stage") == stage) {
                        const char *dep = json_str(json_get(item, "departureCity"), "label");
                        const char *arr = json_str(json_get(item, "arrivalCity"), "label");
                        double len = json_num(item, "length");
                        const char *type = json_str(item, "type");
                        const char *type_str = (strcasecmp(type,"EQU")==0) ? "TTT" : (strcasecmp(type,"IND")==0) ? "ITT" : (strcasecmp(type,"PAS")==0) ? "Mountain" : (strcasecmp(type,"PLN")==0) ? "Flat" : "Road";
                        snprintf(header, sizeof(header), "Stage %d: %s > %s (%.1fkm, %s)", stage, dep, arr, len, type_str);
                        break;
                    }
                }
            }
            if (root) json_free(root);
        }
    }
    if (header[0] == '\0')
        snprintf(header, sizeof(header), "Stage %d Results", stage);

    printf("\n%s\n", header);
    printf("%-4s %-4s %-26s %-30s %-14s %-10s\n",
           "Pos", "Bib", "Name", "Team", "Time", "Gap");
    printf("----------------------------------------------------------------------------------------\n");

    int limit = top_n > 0 ? top_n : finish->n_rankings;
    if (limit > finish->n_rankings) limit = finish->n_rankings;

    for (int i = 0; i < limit; i++) {
        RankEntry *r = &finish->rankings[i];
        Rider *rider = find_rider(r->bib);
        char name[128] = {0};
        char team[64] = {0};
        if (rider) {
            snprintf(name, sizeof(name), "%s %s", rider->firstname, rider->lastname);
            strncpy(team, rider->team_name, sizeof(team) - 1);
        } else {
            snprintf(name, sizeof(name), "Bib #%d", r->bib);
        }

        char time_str[32], gap_str[32];
        format_time(r->time_ms, time_str, sizeof(time_str));
        format_gap(r->gap_ms, gap_str, sizeof(gap_str));

        printf("%-4d %-4d %-26s %-30s %-14s %-10s\n",
               r->position, r->bib, name, team, time_str, gap_str);
    }

    if (top_n > 0 && top_n < finish->n_rankings) {
        printf("... (%d more)\n", finish->n_rankings - top_n);
    }

    /* Show checkpoints if requested */
    if (show_checkpoints && n_cps > 1) {
        printf("\n--- Checkpoints ---\n");
        /* Sort checkpoints by length */
        for (int i = 0; i < n_cps; i++) {
            for (int j = i + 1; j < n_cps; j++) {
                if (cps[j].length < cps[i].length) {
                    Checkpoint tmp = cps[i];
                    cps[i] = cps[j];
                    cps[j] = tmp;
                }
            }
        }

        printf("%-10s %-10s", "CP", "KM");
        /* Header riders from finish checkpoint */
        Checkpoint *fcp = NULL;
        for (int c = 0; c < n_cps; c++) {
            if (cps[c].length == finish->length) { fcp = &cps[c]; break; }
        }
        if (!fcp) fcp = &cps[n_cps - 1];

        int hdr_limit = fcp->n_rankings < 10 ? fcp->n_rankings : 10;
        for (int r = 0; r < hdr_limit; r++) {
            Rider *rider = find_rider(fcp->rankings[r].bib);
            char short_name[16] = {0};
            if (rider) {
                /* Last name only, truncated */
                strncpy(short_name, rider->lastname, 15);
            } else {
                snprintf(short_name, sizeof(short_name), "#%d", fcp->rankings[r].bib);
            }
            printf(" %-15s", short_name);
        }
        printf("\n");

        for (int c = 0; c < n_cps; c++) {
            Checkpoint *cp = &cps[c];
            printf("CP%-8d %-10.1f", cp->checkpoint, cp->length);
            for (int r = 0; r < hdr_limit; r++) {
                /* Find this bib in this checkpoint */
                bool found = false;
                for (int k = 0; k < cp->n_rankings; k++) {
                    if (cp->rankings[k].bib == fcp->rankings[r].bib) {
                        char t[32];
                        format_time(cp->rankings[k].time_ms, t, sizeof(t));
                        /* Show just the gap or position */
                        char gap[16];
                        format_gap(cp->rankings[k].gap_ms, gap, sizeof(gap));
                        if (gap[0]) {
                            printf(" %-15s", gap);
                        } else {
                            printf(" %-15s", t + 3); /* skip hours */
                        }
                        found = true;
                        break;
                    }
                }
                if (!found) printf(" %-15s", "-");
            }
            printf("\n");
        }
    }
}

/* ----- Live telemetry data ----- */
#define MAX_LIVE_RIDERS 256
typedef struct {
    int bib;
    double kph;
    double kph_avg;
    double km_to_finish;
    double lat, lon;
    double gradient;
    double deg_c;
    double kph_wind;
    int wind_dir;
    char status[16];
    char jersey[8];
    char team[8];
    bool is_leader;
} LiveRider;

typedef struct {
    int ygpw[4];        /* Yellow, Green, Polka, White bib numbers */
    bool race_status;   /* true = race in progress */
    int timestamp;
    LiveRider riders[MAX_LIVE_RIDERS];
    int n_riders;
} Telemetry;

static bool fetch_telemetry(Telemetry *tel) {
    memset(tel, 0, sizeof(Telemetry));

    char url[256];
    snprintf(url, sizeof(url), BASE_URL "telemetryCompetitor-%d", YEAR);
    char *raw = fetch_url(url);
    if (!raw) return false;

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);
    if (!root || root->type != JSON_ARRAY || root->count == 0) {
        if (root) json_free(root);
        return false;
    }

    JsonValue *data = root->pairs[0].value;
    tel->race_status = json_get(data, "RaceStatus") && json_get(data, "RaceStatus")->boolean;
    tel->timestamp = (int)json_num(data, "TimeStamp");

    JsonValue *ygpw = json_get(data, "YGPW");
    if (ygpw && ygpw->type == JSON_ARRAY) {
        for (int i = 0; i < 4 && i < ygpw->count; i++)
            tel->ygpw[i] = (int)ygpw->pairs[i].value->num;
    }

    JsonValue *riders = json_get(data, "Riders");
    if (riders && riders->type == JSON_ARRAY) {
        tel->n_riders = 0;
        for (int i = 0; i < riders->count && tel->n_riders < MAX_LIVE_RIDERS; i++) {
            JsonValue *r = riders->pairs[i].value;
            LiveRider *lr = &tel->riders[tel->n_riders++];
            lr->bib = json_int(r, "Bib");
            lr->kph = json_num(r, "kph");
            lr->kph_avg = json_num(r, "kphAvg");
            lr->km_to_finish = json_num(r, "kmToFinish");
            lr->lat = json_num(r, "Latitude");
            lr->lon = json_num(r, "Longitude");
            lr->gradient = json_num(r, "Gradient");
            lr->deg_c = json_num(r, "degC");
            lr->kph_wind = json_num(r, "kphWind");
            lr->wind_dir = json_int(r, "RiderWindDir");
            lr->is_leader = json_get(r, "isLeader") && json_get(r, "isLeader")->boolean;
            const char *st = json_str(r, "Status");
            strncpy(lr->status, st, sizeof(lr->status) - 1);
            const char *jr = json_str(r, "Jersey");
            strncpy(lr->jersey, jr, sizeof(lr->jersey) - 1);
            const char *tm = json_str(r, "team");
            strncpy(lr->team, tm, sizeof(lr->team) - 1);
        }
    }

    json_free(root);
    return true;
}

/* Group detection: riders within ~200m of each other are in the same group */
typedef struct {
    double km_to_finish;  /* average position of group */
    int n_riders;
    int bibs[MAX_LIVE_RIDERS];
    double min_kph;
    double max_kph;
} LiveGroup;

static void detect_groups(Telemetry *tel, LiveGroup *groups, int *n_groups) {
    *n_groups = 0;
    if (tel->n_riders == 0) return;

    /* Sort riders by km_to_finish (ascending = closest to finish first) */
    int order[MAX_LIVE_RIDERS];
    for (int i = 0; i < tel->n_riders; i++) order[i] = i;
    for (int i = 0; i < tel->n_riders - 1; i++) {
        for (int j = i + 1; j < tel->n_riders; j++) {
            if (tel->riders[order[j]].km_to_finish < tel->riders[order[i]].km_to_finish) {
                int tmp = order[i]; order[i] = order[j]; order[j] = tmp;
            }
        }
    }

    /* Group riders within 0.15km of each other */
    LiveGroup *cur = &groups[(*n_groups)++];
    memset(cur, 0, sizeof(LiveGroup));
    cur->km_to_finish = tel->riders[order[0]].km_to_finish;
    cur->n_riders = 0;
    cur->min_kph = 999;
    cur->max_kph = 0;
    cur->bibs[cur->n_riders++] = tel->riders[order[0]].bib;

    for (int i = 1; i < tel->n_riders; i++) {
        LiveRider *r = &tel->riders[order[i]];
        if (fabs(r->km_to_finish - cur->km_to_finish) < 0.15) {
            /* Same group */
            cur->bibs[cur->n_riders++] = r->bib;
            if (r->kph < cur->min_kph) cur->min_kph = r->kph;
            if (r->kph > cur->max_kph) cur->max_kph = r->kph;
        } else {
            /* New group */
            cur = &groups[(*n_groups)++];
            memset(cur, 0, sizeof(LiveGroup));
            cur->km_to_finish = r->km_to_finish;
            cur->n_riders = 0;
            cur->min_kph = r->kph;
            cur->max_kph = r->kph;
            cur->bibs[cur->n_riders++] = r->bib;
        }
    }
}

static void cmd_live(bool watch_mode, int interval_sec) {
    load_riders_and_teams();

    while (true) {
        Telemetry tel;
        if (!fetch_telemetry(&tel)) {
            fprintf(stderr, "Error: could not fetch live telemetry. Race may not be in progress.\n");
            return;
        }

        if (watch_mode) {
            printf("\033[2J\033[H"); /* clear screen */
        }

        printf("Tour de France %d - LIVE Race State\n", YEAR);
        if (tel.race_status)
            printf("Race Status: IN PROGRESS\n");
        else
            printf("Race Status: Finished/Not Started\n");

        /* Jersey holders */
        const char *jersey_names[] = {"Yellow", "Green", "Polka", "White"};
        const char *jersey_codes[] = {"Y", "G", "P", "W"};
        printf("Jerseys: ");
        for (int i = 0; i < 4; i++) {
            if (tel.ygpw[i] > 0) {
                Rider *r = find_rider(tel.ygpw[i]);
                if (r)
                    printf("%s(%s)=%s %s  ", jersey_names[i], jersey_codes[i], r->firstname, r->lastname);
                else
                    printf("%s=%d  ", jersey_names[i], tel.ygpw[i]);
            }
        }
        printf("\n");

        /* Weather from first rider */
        if (tel.n_riders > 0) {
            LiveRider *r = &tel.riders[0];
            printf("Conditions: %.1f°C, Wind %.1f kph\n", r->deg_c, r->kph_wind);
        }

        /* Detect groups */
        LiveGroup groups[MAX_LIVE_RIDERS];
        int n_groups = 0;
        detect_groups(&tel, groups, &n_groups);

        printf("\nGroups on Course (%d groups, %d riders tracked):\n", n_groups, tel.n_riders);
        printf("%-6s %-8s %-6s %-6s %-30s\n", "Group", "kmToFin", "Riders", "kph", "Key Riders");
        printf("--------------------------------------------------------------------\n");

        for (int g = 0; g < n_groups; g++) {
            LiveGroup *grp = &groups[g];
            double avg_kph = grp->n_riders > 0 ? (grp->min_kph + grp->max_kph) / 2.0 : 0;

            /* Build rider names string */
            char names[256] = {0};
            int names_len = 0;
            for (int r = 0; r < grp->n_riders && names_len < 200; r++) {
                Rider *rider = find_rider(grp->bibs[r]);
                char snippet[64];
                if (rider) {
                    snprintf(snippet, sizeof(snippet), "%s %s", rider->firstname, rider->lastname);
                    /* Truncate to last name only for brevity */
                    char *last = strrchr(snippet, ' ');
                    if (last) last++; else last = snippet;
                    snprintf(snippet, sizeof(snippet), "%s", last);
                } else {
                    snprintf(snippet, sizeof(snippet), "#%d", grp->bibs[r]);
                }
                int slen = strlen(snippet);
                if (names_len + slen + 2 < 250) {
                    if (names_len > 0) { strcat(names, ", "); names_len += 2; }
                    strcat(names, snippet);
                    names_len += slen;
                }
            }

            printf("%-6d %-8.2f %-6d %-6.1f %-30s\n",
                   g + 1, grp->km_to_finish, grp->n_riders, avg_kph, names);
        }

        /* Show all riders detail for small fields (TTT) or top riders */
        if (tel.n_riders <= 30) {
            printf("\nAll Riders:\n");
            printf("%-4s %-4s %-26s %-6s %-6s %-7s %-6s %-8s %-5s\n",
                   "Bib", "Team", "Name", "kph", "kmFin", "Grad%", "Wind", "Status", "Lead");
            printf("-------------------------------------------------------------------------------\n");
            int order[MAX_LIVE_RIDERS];
            for (int i = 0; i < tel.n_riders; i++) order[i] = i;
            for (int i = 0; i < tel.n_riders - 1; i++) {
                for (int j = i + 1; j < tel.n_riders; j++) {
                    if (tel.riders[order[j]].km_to_finish < tel.riders[order[i]].km_to_finish) {
                        int tmp = order[i]; order[i] = order[j]; order[j] = tmp;
                    }
                }
            }
            for (int i = 0; i < tel.n_riders; i++) {
                LiveRider *r = &tel.riders[order[i]];
                Rider *rider = find_rider(r->bib);
                char name[128] = {0};
                if (rider)
                    snprintf(name, sizeof(name), "%s %s", rider->firstname, rider->lastname);
                else
                    snprintf(name, sizeof(name), "Bib #%d", r->bib);
                printf("%-4d %-4s %-26s %-6.1f %-6.2f %-7.1f %-6.1f %-8s %s\n",
                       r->bib, r->team, name, r->kph, r->km_to_finish,
                       r->gradient, r->kph_wind, r->status,
                       r->is_leader ? " *" : "");
            }
        }

        if (!watch_mode) break;

        printf("\n(Refreshing every %ds - Ctrl+C to exit)\n", interval_sec);
        fflush(stdout);
        sleep(interval_sec);
    }
}

/* ----- Jersey classifications ----- */
static void cmd_jerseys(int stage) {
    load_riders_and_teams();

    /* Use telemetry YGPW for current jersey holders */
    Telemetry tel;
    if (fetch_telemetry(&tel)) {
        const char *jersey_names[] = {"YELLOW (GC)", "GREEN (Points)", "POLKA DOT (KOM)", "WHITE (U25)"};
        const char *jersey_emoji[] = {"🟡", "🟢", "🔴", "⚪"};
        printf("Tour de France %d - Jersey Holders", YEAR);
        if (stage > 0) printf(" (after Stage %d)", stage);
        printf("\n\n");

        for (int i = 0; i < 4; i++) {
            if (tel.ygpw[i] > 0) {
                Rider *r = find_rider(tel.ygpw[i]);
                if (r) {
                    printf("%s %-18s %s %s  (%s, bib %d)\n",
                           jersey_emoji[i], jersey_names[i],
                           r->firstname, r->lastname, r->team_name, r->bib);
                } else {
                    printf("%s %-18s Bib #%d\n", jersey_emoji[i], jersey_names[i], tel.ygpw[i]);
                }
            }
        }
    } else {
        fprintf(stderr, "Could not fetch jersey data.\n");
    }
}

/* ----- Checkpoint locations ----- */
static void cmd_checkpoints(int stage) {
    char url[256];
    snprintf(url, sizeof(url), BASE_URL "checkpointList-%d-%d", YEAR, stage);
    char *raw = fetch_url(url);
    if (!raw) {
        fprintf(stderr, "Error: could not fetch checkpoint data for stage %d\n", stage);
        return;
    }

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);
    if (!root || root->type != JSON_ARRAY) {
        if (root) json_free(root);
        return;
    }

    /* Sort by length (distance from start) */
    int indices[MAX_CHECKPOINTS * 4];
    int n = root->count < (int)(sizeof(indices)/sizeof(indices[0])) ? root->count : (int)(sizeof(indices)/sizeof(indices[0]));
    for (int i = 0; i < n; i++) indices[i] = i;
    for (int i = 0; i < n - 1; i++) {
        for (int j = i + 1; j < n; j++) {
            double li = json_num(root->pairs[indices[i]].value, "length");
            double lj = json_num(root->pairs[indices[j]].value, "length");
            if (lj < li) { int tmp = indices[i]; indices[i] = indices[j]; indices[j] = tmp; }
        }
    }

    printf("Stage %d - Checkpoints (%d)\n", stage, n);
    printf("%-4s %-7s %-8s %-35s %-20s %-12s %-10s\n",
           "CP", "KM", "Type", "Road/Location", "Place", "Schedule", "Climb");
    printf("-------------------------------------------------------------------------------------------------------\n");

    for (int idx = 0; idx < n; idx++) {
        JsonValue *item = root->pairs[indices[idx]].value;
        int cp = json_int(item, "checkpoint");
        double len = json_num(item, "length");
        const char *road = json_str(item, "road");
        const char *place = json_str(item, "place");
        const char *sched = json_str(item, "middleSchedule");

        /* Type code */
        char type_str[16] = {0};
        JsonValue *types = json_get(item, "checkpointTypes");
        if (types && types->type == JSON_ARRAY) {
            for (int t = 0; t < types->count; t++) {
                const char *code = json_str(types->pairs[t].value, "code");
                if (code[0]) { strncat(type_str, code, sizeof(type_str) - strlen(type_str) - 1); }
            }
        }
        if (type_str[0] == '\0') strcpy(type_str, "");

        /* Climb/summit info */
        char climb[64] = {0};
        JsonValue *summits = json_get(item, "checkpointSummits");
        if (summits && summits->type == JSON_ARRAY && summits->count > 0) {
            const char *sname = json_str(json_get(summits->pairs[0].value, "summit"), "name");
            double altitude = json_num(json_get(summits->pairs[0].value, "summit"), "altitude");
            double clength = json_num(summits->pairs[0].value, "length");
            if (sname[0])
                snprintf(climb, sizeof(climb), "%s (%.0fm, %.0fm)", sname, altitude, clength);
        }

        printf("%-4d %-7.1f %-8s %-35.35s %-20.20s %-12s %-10s\n",
               cp, len, type_str, road, place, sched, climb);
    }
    json_free(root);
}

/* ----- Stage profile (elevation/climb summary) ----- */
static void cmd_profile(int stage) {
    /* Checkpoint data has summit info */
    char url[256];
    snprintf(url, sizeof(url), BASE_URL "checkpoint-%d-%d", YEAR, stage);
    char *raw = fetch_url(url);
    if (!raw) {
        fprintf(stderr, "Error: could not fetch stage profile for stage %d\n", stage);
        return;
    }

    const char *p = raw;
    JsonValue *root = parse_json(&p);
    free(raw);
    if (!root || root->type != JSON_ARRAY || root->count == 0) {
        if (root) json_free(root);
        return;
    }

    JsonValue *cpdata = root->pairs[0].value;
    if (!cpdata || cpdata->type != JSON_OBJECT) {
        json_free(root);
        return;
    }

    /* Get stage info for header */
    char header[256] = {0};
    snprintf(url, sizeof(url), BASE_URL "stage-%d", YEAR);
    char *sraw = fetch_url(url);
    if (sraw) {
        const char *sp = sraw;
        JsonValue *sroot = parse_json(&sp);
        free(sraw);
        if (sroot && sroot->type == JSON_ARRAY) {
            for (int i = 0; i < sroot->count; i++) {
                JsonValue *item = sroot->pairs[i].value;
                if (json_int(item, "stage") == stage) {
                    const char *dep = json_str(json_get(item, "departureCity"), "label");
                    const char *arr = json_str(json_get(item, "arrivalCity"), "label");
                    double len = json_num(item, "length");
                    snprintf(header, sizeof(header), "Stage %d: %s > %s (%.1fkm)", stage, dep, arr, len);
                    break;
                }
            }
        }
        if (sroot) json_free(sroot);
    }
    if (header[0] == '\0') snprintf(header, sizeof(header), "Stage %d", stage);

    printf("%s - Profile\n\n", header);

    /* Collect all summits/climbs */
    int n_climbs = 0;
    for (int i = 0; i < cpdata->count; i++) {
        if (!cpdata->pairs[i].key) continue;
        JsonValue *cp = cpdata->pairs[i].value;
        if (!cp || cp->type != JSON_OBJECT) continue;

        JsonValue *summits = json_get(cp, "checkpointSummits");
        if (summits && summits->type == JSON_ARRAY) {
            for (int s = 0; s < summits->count; s++) {
                JsonValue *summit = summits->pairs[s].value;
                JsonValue *summit_info = json_get(summit, "summit");
                if (summit_info) {
                    const char *name = json_str(summit_info, "name");
                    double altitude = json_num(summit_info, "altitude");
                    double length = json_num(summit, "length");
                    const char *code = json_str(summit, "code");
                    double cp_length = json_num(cp, "length");

                    const char *cat = "C";
                    if (strcmp(code, "H") == 0) cat = "HC";
                    else if (strcmp(code, "1") == 0) cat = "Cat 1";
                    else if (strcmp(code, "2") == 0) cat = "Cat 2";
                    else if (strcmp(code, "3") == 0) cat = "Cat 3";
                    else if (strcmp(code, "4") == 0) cat = "Cat 4";
                    else if (strcmp(code, "X") == 0) cat = "Climb";

                    printf("  %-6s at km %-6.1f  %-40s %4.0fm  length: %.0fm\n",
                           cat, cp_length, name, altitude, length);
                    n_climbs++;
                }
            }
        }

        /* Check for sprint/intermediate points */
        JsonValue *types = json_get(cp, "checkpointTypes");
        if (types && types->type == JSON_ARRAY) {
            for (int t = 0; t < types->count; t++) {
                const char *ttype = json_str(types->pairs[t].value, "type");
                if (strcmp(ttype, "chrono") == 0) {
                    double cp_length = json_num(cp, "length");
                    const char *place = json_str(cp, "place");
                    printf("  %-6s at km %-6.1f  %-40s\n", "CHRONO", cp_length, place);
                }
            }
        }
    }

    if (n_climbs == 0) {
        printf("  No categorised climbs on this stage.\n");
    }

    json_free(root);
}

static void print_usage(const char *prog) {
    fprintf(stderr, "tdf - Tour de France %d results & live tracker\n\n", YEAR);
    fprintf(stderr, "Usage: %s [stage] [options]\n\n", prog);
    fprintf(stderr, "Results:\n");
    fprintf(stderr, "  %s                  Show results for latest completed stage\n", prog);
    fprintf(stderr, "  %s <N>              Show results for stage N (1-21)\n", prog);
    fprintf(stderr, "  %s <N> --top <M>    Show top M riders for stage N\n", prog);
    fprintf(stderr, "  %s <N> --cp         Show checkpoint splits for stage N\n", prog);
    fprintf(stderr, "  %s --gc             Show general classification\n", prog);
    fprintf(stderr, "  %s --gc --top 5     GC top 5\n", prog);
    fprintf(stderr, "\nLive:\n");
    fprintf(stderr, "  %s --live           Live race state (groups, speeds, GPS)\n", prog);
    fprintf(stderr, "  %s --live --watch   Auto-refresh every 15s\n", prog);
    fprintf(stderr, "  %s --live --watch 30  Auto-refresh every 30s\n", prog);
    fprintf(stderr, "  %s --jerseys        Current jersey holders\n", prog);
    fprintf(stderr, "\nInfo:\n");
    fprintf(stderr, "  %s --stages         List all 21 stages\n", prog);
    fprintf(stderr, "  %s --teams          List all teams\n", prog);
    fprintf(stderr, "  %s --riders         List all 184 riders\n", prog);
    fprintf(stderr, "  %s --checkpoints <N>  Checkpoint locations for stage N\n", prog);
    fprintf(stderr, "  %s --profile <N>    Stage profile (climbs, sprints)\n", prog);
    fprintf(stderr, "\n");
}

int main(int argc, char *argv[]) {
    curl_global_init(CURL_GLOBAL_DEFAULT);

    int stage = -1;       /* -1 = not specified, show latest */
    int top_n = 0;        /* 0 = all */
    bool show_cp = false;
    bool show_stages = false;
    bool show_teams = false;
    bool show_riders = false;
    bool show_gc = false;
    bool show_live = false;
    bool show_jerseys = false;
    bool show_checkpoints = false;
    bool show_profile = false;
    bool watch_mode = false;
    int watch_interval = 15;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            print_usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "--stages") == 0) {
            show_stages = true;
        } else if (strcmp(argv[i], "--teams") == 0) {
            show_teams = true;
        } else if (strcmp(argv[i], "--riders") == 0) {
            show_riders = true;
        } else if (strcmp(argv[i], "--gc") == 0) {
            show_gc = true;
        } else if (strcmp(argv[i], "--live") == 0) {
            show_live = true;
        } else if (strcmp(argv[i], "--jerseys") == 0) {
            show_jerseys = true;
        } else if (strcmp(argv[i], "--checkpoints") == 0) {
            show_checkpoints = true;
        } else if (strcmp(argv[i], "--profile") == 0) {
            show_profile = true;
        } else if (strcmp(argv[i], "--cp") == 0) {
            show_cp = true;
        } else if (strcmp(argv[i], "--watch") == 0) {
            watch_mode = true;
            /* Optional interval as next arg */
            if (i + 1 < argc && isdigit((unsigned char)argv[i+1][0])) {
                watch_interval = atoi(argv[++i]);
            }
        } else if (strcmp(argv[i], "--top") == 0) {
            if (i + 1 < argc) {
                top_n = atoi(argv[++i]);
            }
        } else if (isdigit((unsigned char)argv[i][0])) {
            stage = atoi(argv[i]);
        } else {
            fprintf(stderr, "Unknown option: %s\n", argv[i]);
            print_usage(argv[0]);
            return 1;
        }
    }

    if (show_live) {
        cmd_live(watch_mode, watch_interval);
    } else if (show_jerseys) {
        if (stage < 0) stage = find_latest_stage();
        cmd_jerseys(stage);
    } else if (show_checkpoints) {
        if (stage < 0) stage = find_latest_stage();
        if (stage < 1) stage = 1;
        if (stage > 21) stage = 21;
        cmd_checkpoints(stage);
    } else if (show_profile) {
        if (stage < 0) stage = find_latest_stage();
        if (stage < 1) stage = 1;
        if (stage > 21) stage = 21;
        cmd_profile(stage);
    } else if (show_stages) {
        cmd_stages();
    } else if (show_teams) {
        cmd_teams();
    } else if (show_riders) {
        cmd_riders();
    } else if (show_gc) {
        /* GC is the same as stage result but using rankingType (general classification) */
        /* For now, show the latest stage's rankingType */
        if (stage < 0) stage = find_latest_stage();
        if (stage < 1) stage = 1;
        /* Try rankingType for GC */
        Checkpoint cps[MAX_CHECKPOINTS];
        int n_cps = 0;
        int rc = fetch_stage_rankings(stage, cps, &n_cps, "rankingType");
        if (rc == 0 && n_cps > 0) {
            int finish_idx = 0;
            for (int i = 1; i < n_cps; i++) {
                if (cps[i].length > cps[finish_idx].length) finish_idx = i;
            }
            Checkpoint *finish = &cps[finish_idx];
            printf("\nGeneral Classification after Stage %d\n", stage);
            printf("%-4s %-4s %-26s %-30s %-14s %-10s\n",
                   "Pos", "Bib", "Name", "Team", "Time", "Gap");
            printf("----------------------------------------------------------------------------------------\n");
            load_riders_and_teams();
            int limit = top_n > 0 ? top_n : finish->n_rankings;
            if (limit > finish->n_rankings) limit = finish->n_rankings;
            for (int i = 0; i < limit; i++) {
                RankEntry *r = &finish->rankings[i];
                Rider *rider = find_rider(r->bib);
                char name[128] = {0};
                char team[64] = {0};
                if (rider) {
                    snprintf(name, sizeof(name), "%s %s", rider->firstname, rider->lastname);
                    strncpy(team, rider->team_name, sizeof(team) - 1);
                } else {
                    snprintf(name, sizeof(name), "Bib #%d", r->bib);
                }
                char time_str[32], gap_str[32];
                format_time(r->time_ms, time_str, sizeof(time_str));
                format_gap(r->gap_ms, gap_str, sizeof(gap_str));
                printf("%-4d %-4d %-26s %-30s %-14s %-10s\n",
                       r->position, r->bib, name, team, time_str, gap_str);
            }
        } else {
            fprintf(stderr, "GC data not yet available for stage %d\n", stage);
        }
    } else {
        /* Default: show stage result */
        if (stage < 0) stage = find_latest_stage();
        if (stage < 1) stage = 1;
        if (stage > 21) stage = 21;
        print_stage_result(stage, top_n, show_cp);
    }

    curl_global_cleanup();
    return 0;
}
