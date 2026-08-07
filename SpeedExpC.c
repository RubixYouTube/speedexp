/*
 * SpeedExp.c - A program for helping speedy collabs
 * C translation of SpeedExp.py with optimizations
 *
 * Compile: gcc -O2 -o SpeedExp SpeedExp.c -lm
 * Requirements: ffmpeg, ffprobe in PATH
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#include <unistd.h>
#include <time.h>
#include <ctype.h>
#include <limits.h>

/* ─────────────────────────────────────────────────────────────────
 * Constants
 * ───────────────────────────────────────────────────────────────── */
#define FIXED_PITCH_RATIO       1.059463094352953
#define SPECIAL_PITCH_UP        1.4983070768766815
#define SPECIAL_PITCH_DOWN      0.7491535384383408
#define DEFAULT_TEXT_SIZE       111
#define DEFAULT_WATERMARK_SIZE  60
#define MAX_SPEED_RETRIES       6
#define TARGET_VOLUME_DEFAULT   -20.0

#define MAX_PATH_LEN   4096
#define MAX_CMD_LEN    8192
#define MAX_STR_LEN    1024
#define MAX_EXPORTS    64
#define MAX_DIRS       256

static int g_use_color = 1;
#define CLR_RED    (g_use_color ? "\033[31m" : "")
#define CLR_GRN    (g_use_color ? "\033[32m" : "")
#define CLR_YLW    (g_use_color ? "\033[33m" : "")
#define CLR_RST    (g_use_color ? "\033[0m"  : "")

/* ─────────────────────────────────────────────────────────────────
 * Data structures
 * ───────────────────────────────────────────────────────────────── */
typedef struct {
    double duration;
    long   size;
    long   bitrate;
    char   video_codec[64];
    char   audio_codec[64];
    int    width;
    int    height;
    int    has_audio;
    double fps;
} VideoInfo;

typedef struct {
    int  height;
    char label[16];
} QualityOption;

static const QualityOption QUALITY_OPTIONS[] = {
    {144,  "144p"},
    {360,  "360p"},
    {480,  "480p"},
    {720,  "720p"},
    {1080, "1080p"},
};
#define N_QUALITY_OPTIONS 5

typedef struct {
    char name[64];
    char codec[32];
    char params[16][64];
    int  n_params;
} CodecConfig;

static int g_codecs_cached = 0;
static int g_codec_libx264 = 0;
static int g_codec_h264    = 0;
static int g_codec_mpeg4   = 0;
static int g_codec_libx265 = 0;
static int g_codec_ffv1    = 0;

/* ─────────────────────────────────────────────────────────────────
 * Run command and capture output
 * ───────────────────────────────────────────────────────────────── */
static char *run_cmd_capture(const char *cmd, int *ret_code)
{
    FILE *fp = popen(cmd, "r");
    if (!fp) {
        if (ret_code) *ret_code = -1;
        return strdup("");
    }
    size_t cap = 4096, len = 0;
    char  *buf = malloc(cap);
    if (!buf) {
        pclose(fp);
        if (ret_code) *ret_code = -1;
        return NULL;
    }
    int c;
    while ((c = fgetc(fp)) != EOF) {
        if (len + 1 >= cap) {
            cap *= 2;
            char *nb = realloc(buf, cap);
            if (!nb) {
                free(buf);
                pclose(fp);
                if (ret_code) *ret_code = -1;
                return NULL;
            }
            buf = nb;
        }
        buf[len++] = (char)c;
    }
    buf[len] = '\0';
    int rc = pclose(fp);
    if (ret_code) *ret_code = rc;
    return buf;
}

static int run_cmd(const char *cmd)
{
    return system(cmd);
}

/* ─────────────────────────────────────────────────────────────────
 * String helpers
 * ───────────────────────────────────────────────────────────────── */
static void str_trim(char *s)
{
    char *p = s;
    while (*p && isspace((unsigned char)*p)) p++;
    if (p != s) memmove(s, p, strlen(p) + 1);
    size_t n = strlen(s);
    while (n > 0 && isspace((unsigned char)s[n-1])) s[--n] = '\0';
}

static void str_toupper(char *s)
{
    for (; *s; s++) *s = (char)toupper((unsigned char)*s);
}

static int str_isdigits(const char *s)
{
    if (!s || !*s) return 0;
    for (; *s; s++) if (!isdigit((unsigned char)*s)) return 0;
    return 1;
}

static int str_contains_ci(const char *haystack, const char *needle)
{
    if (!haystack || !needle) return 0;
    size_t hn = strlen(needle);
    size_t hs = strlen(haystack);
    if (hn > hs) return 0;
    for (size_t i = 0; i <= hs - hn; i++) {
        size_t j;
        for (j = 0; j < hn; j++)
            if (tolower((unsigned char)haystack[i+j]) !=
                tolower((unsigned char)needle[j])) break;
        if (j == hn) return 1;
    }
    return 0;
}

/* ─────────────────────────────────────────────────────────────────
 * File / path helpers
 * ───────────────────────────────────────────────────────────────── */
static int file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

static int is_directory(const char *path)
{
    struct stat st;
    return (stat(path, &st) == 0 && S_ISDIR(st.st_mode));
}

static long file_size(const char *path)
{
    struct stat st;
    return (stat(path, &st) == 0) ? (long)st.st_size : 0L;
}

static const char *get_file_extension(int smooth_mode)
{
    return smooth_mode ? ".mov" : ".mp4";
}

static const char *path_basename(const char *path)
{
    const char *p = strrchr(path, '/');
    return p ? p + 1 : path;
}

static int mkdirs(const char *path)
{
    char tmp[MAX_PATH_LEN];
    snprintf(tmp, sizeof(tmp), "%s", path);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            mkdir(tmp, 0755);
            *p = '/';
        }
    }
    return mkdir(tmp, 0755);
}

/* ─────────────────────────────────────────────────────────────────
 * Power notation formatter
 * ───────────────────────────────────────────────────────────────── */
static void format_power_notation(long long number, char *out, size_t out_sz)
{
    if (number < 1000000LL) {
        snprintf(out, out_sz, "%lld", number);
        return;
    }
    int exponent = (int)floor(log10((double)number));
    double mantissa = number / pow(10.0, exponent);
    snprintf(out, out_sz, "%.2f * 10^%d", mantissa, exponent);
}

/* ─────────────────────────────────────────────────────────────────
 * Special pitch helpers
 * ───────────────────────────────────────────────────────────────── */
static void get_special_pitch_for_iteration(int iteration,
                                            double *ratio,
                                            char *semitones,
                                            size_t sem_sz)
{
    if (iteration % 2 == 0) {
        *ratio = SPECIAL_PITCH_UP;
        snprintf(semitones, sem_sz, "+7");
    } else {
        *ratio = SPECIAL_PITCH_DOWN;
        snprintf(semitones, sem_sz, "-5");
    }
}

/* ─────────────────────────────────────────────────────────────────
 * FFmpeg text escaping
 * ───────────────────────────────────────────────────────────────── */
static void escape_text_for_ffmpeg(const char *in, char *out, size_t out_sz)
{
    size_t j = 0;
    for (size_t i = 0; in[i] && j + 4 < out_sz; i++) {
        char c = in[i];
        if (c == '\\' || c == ':' || c == '\'' ||
            c == '[' || c == ']' || c == ',' || c == ';') {
            out[j++] = '\\';
        }
        out[j++] = c;
    }
    out[j] = '\0';
}

/* ─────────────────────────────────────────────────────────────────
 * Dependency checks
 * ───────────────────────────────────────────────────────────────── */
static int which(const char *prog)
{
    char cmd[MAX_STR_LEN];
    snprintf(cmd, sizeof(cmd), "which %s > /dev/null 2>&1", prog);
    return system(cmd) == 0;
}

static void check_dependencies(int *has_rubberband, int *has_loudnorm)
{
    if (!which("ffmpeg")) {
        fprintf(stderr, "❌ ffmpeg is not installed or not in PATH.\n");
        exit(1);
    }
    if (!which("ffprobe")) {
        fprintf(stderr, "❌ ffprobe is not installed or not in PATH.\n");
        exit(1);
    }
    printf("%s✓%s FFmpeg found\n", CLR_GRN, CLR_RST);
    printf("%s✓%s FFprobe found\n", CLR_GRN, CLR_RST);

    int rc = 0;
    char *out = run_cmd_capture("ffmpeg -filters 2>&1", &rc);
    if (out) {
        *has_rubberband = str_contains_ci(out, "rubberband");
        *has_loudnorm   = str_contains_ci(out, "loudnorm");
        free(out);
    } else {
        *has_rubberband = 0;
        *has_loudnorm   = 0;
    }
    printf("%s%s Rubberband filter %s%s\n",
           *has_rubberband ? CLR_GRN : CLR_YLW,
           *has_rubberband ? "✓" : "⚠",
           *has_rubberband ? "available" : "NOT available",
           CLR_RST);
    printf("%s%s Loudnorm filter %s%s\n",
           *has_loudnorm ? CLR_GRN : CLR_YLW,
           *has_loudnorm ? "✓" : "⚠",
           *has_loudnorm ? "available" : "NOT available",
           CLR_RST);
}

static void get_ffmpeg_version(char *out, size_t out_sz)
{
    int rc;
    char *buf = run_cmd_capture("ffmpeg -version 2>&1", &rc);
    if (!buf) {
        snprintf(out, out_sz, "Unknown");
        return;
    }
    char *nl = strchr(buf, '\n');
    if (nl) *nl = '\0';
    snprintf(out, out_sz, "%s", buf);
    free(buf);
}

/* ─────────────────────────────────────────────────────────────────
 * Codec detection (cached)
 * ───────────────────────────────────────────────────────────────── */
static void get_available_codecs(void)
{
    if (g_codecs_cached) return;
    int rc;
    char *out = run_cmd_capture("ffmpeg -codecs 2>&1", &rc);
    if (!out) {
        g_codec_libx264 = 1;
        g_codec_mpeg4   = 1;
        g_codecs_cached = 1;
        return;
    }
    g_codec_libx264 = str_contains_ci(out, "libx264") ||
                      str_contains_ci(out, "H.264");
    g_codec_h264    = str_contains_ci(out, "h264");
    g_codec_mpeg4   = str_contains_ci(out, "mpeg4");
    g_codec_libx265 = str_contains_ci(out, "libx265");
    g_codec_ffv1    = str_contains_ci(out, "ffv1");
    free(out);
    g_codecs_cached = 1;
    printf("  Available codecs:");
    if (g_codec_libx264) printf(" libx264");
    if (g_codec_h264)    printf(" h264");
    if (g_codec_mpeg4)   printf(" mpeg4");
    if (g_codec_libx265) printf(" libx265");
    if (g_codec_ffv1)    printf(" ffv1");
    printf("\n");
}

static int select_codec_configs(const char *preset, int smooth_mode,
                                CodecConfig *configs, int max_configs)
{
    get_available_codecs();
    int n = 0;

    if (smooth_mode) {
        if (n < max_configs) {
            CodecConfig *c = &configs[n++];
            snprintf(c->name,     sizeof(c->name),     "H.264 Lossless");
            snprintf(c->codec,    sizeof(c->codec),    "libx264");
            snprintf(c->params[0], 64, "-crf");
            snprintf(c->params[1], 64, "0");
            snprintf(c->params[2], 64, "-preset");
            snprintf(c->params[3], 64, "%s", preset);
            snprintf(c->params[4], 64, "-pix_fmt");
            snprintf(c->params[5], 64, "yuv420p");
            c->n_params = 6;
        }
        if (n < max_configs) {
            CodecConfig *c = &configs[n++];
            snprintf(c->name,     sizeof(c->name),     "H.264 High Quality");
            snprintf(c->codec,    sizeof(c->codec),    "libx264");
            snprintf(c->params[0], 64, "-crf");
            snprintf(c->params[1], 64, "10");
            snprintf(c->params[2], 64, "-preset");
            snprintf(c->params[3], 64, "%s", preset);
            snprintf(c->params[4], 64, "-pix_fmt");
            snprintf(c->params[5], 64, "yuv420p");
            c->n_params = 6;
        }
    } else {
        if ((g_codec_libx264 || g_codec_h264) && n < max_configs) {
            CodecConfig *c = &configs[n++];
            snprintf(c->name,      sizeof(c->name),  "H.264 Baseline");
            snprintf(c->codec,     sizeof(c->codec), "libx264");
            snprintf(c->params[0], 64, "-profile:v");
            snprintf(c->params[1], 64, "baseline");
            snprintf(c->params[2], 64, "-level");
            snprintf(c->params[3], 64, "3.0");
            snprintf(c->params[4], 64, "-pix_fmt");
            snprintf(c->params[5], 64, "yuv420p");
            snprintf(c->params[6], 64, "-preset");
            snprintf(c->params[7], 64, "%s", preset);
            c->n_params = 8;
        }
        if (g_codec_libx264 && n < max_configs) {
            CodecConfig *c = &configs[n++];
            snprintf(c->name,      sizeof(c->name),  "H.264 Ultrafast");
            snprintf(c->codec,     sizeof(c->codec), "libx264");
            snprintf(c->params[0], 64, "-pix_fmt");
            snprintf(c->params[1], 64, "yuv420p");
            snprintf(c->params[2], 64, "-preset");
            snprintf(c->params[3], 64, "ultrafast");
            c->n_params = 4;
        }
        if (g_codec_mpeg4 && n < max_configs) {
            CodecConfig *c = &configs[n++];
            snprintf(c->name,      sizeof(c->name),  "MPEG4");
            snprintf(c->codec,     sizeof(c->codec), "mpeg4");
            snprintf(c->params[0], 64, "-q:v");
            snprintf(c->params[1], 64, "5");
            snprintf(c->params[2], 64, "-pix_fmt");
            snprintf(c->params[3], 64, "yuv420p");
            c->n_params = 4;
        }
        if (n < max_configs) {
            CodecConfig *c = &configs[n++];
            snprintf(c->name,      sizeof(c->name),  "Fallback");
            snprintf(c->codec,     sizeof(c->codec), "libx264");
            snprintf(c->params[0], 64, "-pix_fmt");
            snprintf(c->params[1], 64, "yuv420p");
            c->n_params = 2;
        }
    }
    return n;
}

/* ─────────────────────────────────────────────────────────────────
 * Video probing helpers
 * ───────────────────────────────────────────────────────────────── */
static double ffprobe_get_duration(const char *path)
{
    char cmd[MAX_CMD_LEN];
    snprintf(cmd, sizeof(cmd),
        "ffprobe -v error -show_entries format=duration"
        " -of default=noprint_wrappers=1:nokey=1 \"%s\" 2>/dev/null", path);
    int rc;
    char *out = run_cmd_capture(cmd, &rc);
    double d = 0.0;
    if (out && rc == 0) d = atof(out);
    free(out);
    return d;
}

static void get_video_info(const char *path, VideoInfo *info)
{
    memset(info, 0, sizeof(*info));
    info->fps = 30.0;
    snprintf(info->video_codec, sizeof(info->video_codec), "unknown");
    snprintf(info->audio_codec, sizeof(info->audio_codec), "unknown");

    char cmd[MAX_CMD_LEN];
    snprintf(cmd, sizeof(cmd),
        "ffprobe -v error -show_format -show_streams"
        " -of json \"%s\" 2>/dev/null", path);
    int rc;
    char *raw = run_cmd_capture(cmd, &rc);
    if (!raw || rc != 0) {
        free(raw);
        return;
    }

    /* duration */
    {
        const char *p = strstr(raw, "\"duration\"");
        if (p) {
            p = strchr(p, ':');
            if (p) info->duration = atof(p + 2);
        }
    }
    /* size */
    {
        const char *p = strstr(raw, "\"size\"");
        if (p) {
            p = strchr(p, ':');
            if (p) info->size = atol(p + 2);
        }
    }
    /* bit_rate */
    {
        const char *p = strstr(raw, "\"bit_rate\"");
        if (p) {
            p = strchr(p, ':');
            if (p) info->bitrate = atol(p + 2);
        }
    }

    /* video stream */
    {
        const char *vp = raw;
        while ((vp = strstr(vp, "\"codec_type\"")) != NULL) {
            const char *type_val = strchr(vp, ':');
            if (type_val && strstr(type_val, "\"video\"") &&
                (strstr(type_val, "\"video\"") - type_val < 10)) {

                const char *search_start = (vp - 300 > raw) ? vp - 300 : raw;

                const char *cn = strstr(search_start, "\"codec_name\"");
                if (cn && cn < vp + 300) {
                    cn = strchr(cn, ':');
                    if (cn) {
                        cn += 2;
                        while (*cn == '"') cn++;
                        char *end = strchr(cn, '"');
                        if (end) {
                            size_t l = (size_t)(end - cn);
                            if (l >= sizeof(info->video_codec))
                                l = sizeof(info->video_codec) - 1;
                            strncpy(info->video_codec, cn, l);
                            info->video_codec[l] = '\0';
                        }
                    }
                }
                const char *wp = strstr(vp, "\"width\"");
                if (wp) {
                    wp = strchr(wp, ':');
                    if (wp) info->width = atoi(wp + 2);
                }
                const char *hp = strstr(vp, "\"height\"");
                if (hp) {
                    hp = strchr(hp, ':');
                    if (hp) info->height = atoi(hp + 2);
                }
                const char *fr = strstr(vp, "\"r_frame_rate\"");
                if (fr) {
                    fr = strchr(fr, ':');
                    if (fr) {
                        fr += 2;
                        while (*fr == '"') fr++;
                        char fps_str[32];
                        int i = 0;
                        while (*fr && *fr != '"' && i < 31)
                            fps_str[i++] = *fr++;
                        fps_str[i] = '\0';
                        char *slash = strchr(fps_str, '/');
                        if (slash) {
                            int num = atoi(fps_str);
                            int den = atoi(slash + 1);
                            if (den > 0) info->fps = (double)num / den;
                        } else {
                            info->fps = atof(fps_str);
                        }
                    }
                }
                break;
            }
            vp++;
        }
    }

    /* audio stream */
    {
        const char *ap = raw;
        while ((ap = strstr(ap, "\"codec_type\"")) != NULL) {
            const char *type_val = strchr(ap, ':');
            if (type_val && strstr(type_val, "\"audio\"") &&
                (strstr(type_val, "\"audio\"") - type_val < 10)) {

                info->has_audio = 1;
                const char *search_start = (ap - 300 > raw) ? ap - 300 : raw;
                const char *cn = strstr(search_start, "\"codec_name\"");
                if (cn && cn < ap + 300) {
                    cn = strchr(cn, ':');
                    if (cn) {
                        cn += 2;
                        while (*cn == '"') cn++;
                        char *end = strchr(cn, '"');
                        if (end) {
                            size_t l = (size_t)(end - cn);
                            if (l >= sizeof(info->audio_codec))
                                l = sizeof(info->audio_codec) - 1;
                            strncpy(info->audio_codec, cn, l);
                            info->audio_codec[l] = '\0';
                        }
                    }
                }
                break;
            }
            ap++;
        }
    }
    free(raw);
}

static double get_audio_volume(const char *path)
{
    char cmd[MAX_CMD_LEN];
    snprintf(cmd, sizeof(cmd),
        "ffmpeg -i \"%s\" -af volumedetect -vn -f null - 2>&1", path);
    int rc;
    char *out = run_cmd_capture(cmd, &rc);
    double vol = -20.0;
    if (out) {
        char *p = strstr(out, "mean_volume:");
        if (p) {
            p += 12;
            while (*p == ' ') p++;
            vol = atof(p);
        }
        free(out);
    }
    return vol;
}

/* ─────────────────────────────────────────────────────────────────
 * File validation
 * ───────────────────────────────────────────────────────────────── */
static int validate_video_file(const char *path, char *err, size_t err_sz)
{
    if (!file_exists(path)) {
        snprintf(err, err_sz, "File not found: %s", path);
        return 0;
    }
    if (is_directory(path)) {
        snprintf(err, err_sz, "Path is a directory: %s", path);
        return 0;
    }
    if (file_size(path) < 1000) {
        snprintf(err, err_sz, "File too small");
        return 0;
    }
    double d = ffprobe_get_duration(path);
    if (d <= 0) {
        snprintf(err, err_sz, "Cannot read duration");
        return 0;
    }
    return 1;
}

static int verify_output_file(const char *path, char *msg, size_t msg_sz)
{
    if (!file_exists(path)) {
        snprintf(msg, msg_sz, "File not created");
        return 0;
    }
    if (file_size(path) < 500) {
        snprintf(msg, msg_sz, "File too small");
        return 0;
    }
    double d = ffprobe_get_duration(path);
    if (d <= 0) {
        snprintf(msg, msg_sz, "Invalid duration");
        return 0;
    }
    snprintf(msg, msg_sz, "Valid");
    return 1;
}

/* ─────────────────────────────────────────────────────────────────
 * Directory / folder helpers
 * ───────────────────────────────────────────────────────────────── */
static const char *MOVIES_PATHS[] = {
    "/data/data/com.termux/files/home/storage/movies",
    "/data/data/com.termux/files/home/storage/shared/Movies",
    "/storage/emulated/0/Movies",
    "/sdcard/Movies",
    NULL
};

typedef struct {
    char name[256];
    char path[MAX_PATH_LEN];
} DirEntry;

static char *get_movies_path(void)
{
    static char home_movies[MAX_PATH_LEN];
    static char cwd_movies[MAX_PATH_LEN];

    for (int i = 0; MOVIES_PATHS[i]; i++)
        if (is_directory(MOVIES_PATHS[i]))
            return (char *)MOVIES_PATHS[i];

    const char *home = getenv("HOME");
    if (home) {
        snprintf(home_movies, sizeof(home_movies), "%s/Movies", home);
        if (is_directory(home_movies)) return home_movies;
    }

    char cwd[MAX_PATH_LEN];
    if (getcwd(cwd, sizeof(cwd))) {
        snprintf(cwd_movies, sizeof(cwd_movies), "%s/Movies", cwd);
        if (is_directory(cwd_movies)) return cwd_movies;
    }
    return NULL;
}

static int list_subdirs(const char *base, DirEntry *entries, int max_entries)
{
    DIR *dp = opendir(base);
    if (!dp) return 0;
    int n = 0;
    struct dirent *de;
    while ((de = readdir(dp)) != NULL && n < max_entries) {
        if (de->d_name[0] == '.') continue;
        char full[MAX_PATH_LEN];
        snprintf(full, sizeof(full), "%s/%s", base, de->d_name);
        if (is_directory(full)) {
            snprintf(entries[n].name, sizeof(entries[n].name), "%s", de->d_name);
            snprintf(entries[n].path, sizeof(entries[n].path), "%s", full);
            n++;
        }
    }
    closedir(dp);
    /* simple alpha sort */
    for (int i = 0; i < n - 1; i++)
        for (int j = i + 1; j < n; j++)
            if (strcmp(entries[i].name, entries[j].name) > 0) {
                DirEntry tmp = entries[i];
                entries[i] = entries[j];
                entries[j] = tmp;
            }
    return n;
}

typedef struct {
    char   path[MAX_PATH_LEN];
    time_t mtime;
    long   size;
} Mp4Entry;

static void find_mp4_recursive(const char *dir, Mp4Entry *entries,
                                int max, int *count)
{
    DIR *dp = opendir(dir);
    if (!dp) return;
    struct dirent *de;
    while ((de = readdir(dp)) != NULL) {
        if (de->d_name[0] == '.') continue;
        char full[MAX_PATH_LEN];
        snprintf(full, sizeof(full), "%s/%s", dir, de->d_name);
        if (is_directory(full)) {
            find_mp4_recursive(full, entries, max, count);
        } else {
            size_t nl = strlen(de->d_name);
            if (nl >= 4 &&
                strcasecmp(de->d_name + nl - 4, ".mp4") == 0) {
                struct stat st;
                if (stat(full, &st) == 0 &&
                    st.st_size > 1000 &&
                    *count < max) {
                    snprintf(entries[*count].path,
                             MAX_PATH_LEN, "%s", full);
                    entries[*count].mtime = st.st_mtime;
                    entries[*count].size  = (long)st.st_size;
                    (*count)++;
                }
            }
        }
    }
    closedir(dp);
}

static int mp4_cmp_mtime(const void *a, const void *b)
{
    const Mp4Entry *ea = a, *eb = b;
    return (ea->mtime < eb->mtime) - (ea->mtime > eb->mtime);
}

static int find_latest_mp4(const char *directory,
                           char *out_path, size_t out_sz)
{
    Mp4Entry *entries = malloc(sizeof(Mp4Entry) * 4096);
    if (!entries) return 0;
    int count = 0;
    find_mp4_recursive(directory, entries, 4096, &count);
    if (count == 0) {
        free(entries);
        return 0;
    }
    qsort(entries, count, sizeof(Mp4Entry), mp4_cmp_mtime);
    snprintf(out_path, out_sz, "%s", entries[0].path);
    free(entries);
    return 1;
}

static int select_video_from_movies(char *out_path, size_t out_sz)
{
    char *movies_path = get_movies_path();
    if (!movies_path) {
        fprintf(stderr,
                "❌ Movies folder not found in any known location\n");
        return 0;
    }
    static DirEntry dirs[MAX_DIRS];
    int n = list_subdirs(movies_path, dirs, MAX_DIRS);
    if (n == 0) {
        fprintf(stderr, "❌ No accessible directories in: %s\n",
                movies_path);
        return 0;
    }

    printf("\n============================================================\n");
    printf("VIDEO EDITOR FOLDERS\n");
    printf("============================================================\n");
    printf("Location: %s\n\n", movies_path);

    for (int i = 0; i < n; i++) {
        int mp4_count = 0;
        DIR *dp = opendir(dirs[i].path);
        if (dp) {
            struct dirent *de;
            while ((de = readdir(dp))) {
                size_t nl = strlen(de->d_name);
                if (nl >= 4 &&
                    strcasecmp(de->d_name + nl - 4, ".mp4") == 0)
                    mp4_count++;
            }
            closedir(dp);
        }
        printf("  [%d] %s (%d mp4 files)\n",
               i + 1, dirs[i].name, mp4_count);
    }
    printf("\n");

    while (1) {
        printf("Select your video editor folder (1-%d): ", n);
        fflush(stdout);
        char buf[64];
        if (!fgets(buf, sizeof(buf), stdin)) return 0;
        str_trim(buf);
        if (!str_isdigits(buf)) {
            printf("  Please enter a number\n");
            continue;
        }
        int sel = atoi(buf);
        if (sel < 1 || sel > n) {
            printf("  Please enter a number between 1 and %d\n", n);
            continue;
        }
        printf("\n  Selected: %s\n", dirs[sel - 1].name);
        printf("  Searching for latest .mp4 file...\n");
        if (!find_latest_mp4(dirs[sel - 1].path, out_path, out_sz)) {
            printf("  ❌ No .mp4 files found\n");
            printf("  Try another folder? (Y/N): ");
            fflush(stdout);
            fgets(buf, sizeof(buf), stdin);
            str_trim(buf);
            str_toupper(buf);
            if (strcmp(buf, "Y") != 0) return 0;
            continue;
        }
        printf("  %s✓%s Found: %s\n",
               CLR_GRN, CLR_RST, path_basename(out_path));
        return 1;
    }
}

/* ─────────────────────────────────────────────────────────────────
 * Exports folder
 * ───────────────────────────────────────────────────────────────── */
static void create_exports_folder(char *out_path, size_t out_sz)
{
    const char *td =
        "/data/data/com.termux/files/home/storage/downloads";
    if (is_directory(td)) {
        snprintf(out_path, out_sz, "%s/Exports", td);
    } else {
        printf("Warning: Termux not detected. Using current directory.\n");
        char cwd[MAX_PATH_LEN];
        if (getcwd(cwd, sizeof(cwd)))
            snprintf(out_path, out_sz, "%s/Exports", cwd);
        else
            snprintf(out_path, out_sz, "Exports");
    }
    mkdirs(out_path);
    printf("'Exports' directory at: %s\n", out_path);
}

static void get_unique_filename(const char *exports_dir,
                                const char *base_name,
                                int smooth_mode,
                                char *out_path, size_t out_sz,
                                char *out_name, size_t out_name_sz)
{
    const char *ext = get_file_extension(smooth_mode);
    snprintf(out_path, out_sz, "%s/%s%s", exports_dir, base_name, ext);
    snprintf(out_name, out_name_sz, "%s", base_name);
    if (!file_exists(out_path)) return;
    for (int id = 1; id <= 999; id++) {
        snprintf(out_name, out_name_sz, "%s-%d", base_name, id);
        snprintf(out_path, out_sz, "%s/%s%s",
                 exports_dir, out_name, ext);
        if (!file_exists(out_path)) return;
    }
    fprintf(stderr, "❌ Too many duplicate files\n");
    exit(1);
}

/* ─────────────────────────────────────────────────────────────────
 * Progress bar
 * ───────────────────────────────────────────────────────────────── */
static void init_progress_bar(void)   { printf("\n\n"); }
static void finish_progress_bar(void) { printf("\n"); }

static void print_progress_bar(int current, int total, int bar_length)
{
    if (total == 0) return;
    double progress = (double)current / total;
    int filled = (int)(bar_length * progress);
    printf("\033[2K\033[1A\033[2K\r[");
    for (int i = 0; i < filled; i++)       printf("█");
    for (int i = filled; i < bar_length; i++) printf("░");
    printf("] %.1f%%\n%d out of %d done.", progress * 100.0,
           current, total);
    fflush(stdout);
}

/* ─────────────────────────────────────────────────────────────────
 * Find existing exports
 * ───────────────────────────────────────────────────────────────── */
typedef struct {
    int  export_num;
    char filename[256];
    char path[MAX_PATH_LEN];
} ExportEntry;

static int find_existing_exports(const char *exports_dir,
                                  ExportEntry *entries,
                                  int max_entries)
{
    if (!is_directory(exports_dir)) return 0;
    DIR *dp = opendir(exports_dir);
    if (!dp) return 0;
    int n = 0;
    struct dirent *de;
    while ((de = readdir(dp)) != NULL && n < max_entries) {
        const char *fn = de->d_name;
        if (strncmp(fn, "export-", 7) != 0) continue;
        const char *num_start = fn + 7;
        if (!isdigit((unsigned char)*num_start)) continue;
        int export_num = atoi(num_start);
        size_t nl = strlen(fn);
        if (nl < 4) continue;
        const char *ext = fn + nl - 4;
        if (strcasecmp(ext, ".mp4") != 0 &&
            strcasecmp(ext, ".mov") != 0) continue;
        char full[MAX_PATH_LEN];
        snprintf(full, sizeof(full), "%s/%s", exports_dir, fn);
        if (!file_exists(full) || file_size(full) < 1000) continue;
        entries[n].export_num = export_num;
        snprintf(entries[n].filename,
                 sizeof(entries[n].filename), "%s", fn);
        snprintf(entries[n].path,
                 sizeof(entries[n].path), "%s", full);
        n++;
    }
    closedir(dp);
    /* sort by export_num */
    for (int i = 0; i < n - 1; i++)
        for (int j = i + 1; j < n; j++)
            if (entries[i].export_num > entries[j].export_num) {
                ExportEntry tmp = entries[i];
                entries[i] = entries[j];
                entries[j] = tmp;
            }
    return n;
}

/* ─────────────────────────────────────────────────────────────────
 * Build speed-up ffmpeg command
 * ───────────────────────────────────────────────────────────────── */
static int build_speedup_command(
        char *cmd_out, size_t cmd_sz,
        const char *input_path, const char *output_path,
        double tempo, double video_pts,
        int enable_pitch, int enable_special_pitch,
        double pitch_ratio,
        int has_rubberband, int has_audio,
        double volume_adjustment,
        double effective_fps, const char *preset,
        int smooth_mode, int target_height)
{
    char scale_filter[64] = "";
    if (target_height > 0)
        snprintf(scale_filter, sizeof(scale_filter),
                 ",scale=-2:%d", target_height);

    const char *video_codec = "libx264";
    char video_params[256];
    const char *audio_codec;

    if (smooth_mode) {
        snprintf(video_params, sizeof(video_params),
                 "-crf 0 -preset %s -pix_fmt yuv420p", preset);
        audio_codec = "pcm_s16le";
    } else {
        snprintf(video_params, sizeof(video_params),
                 "-preset %s -crf 23 -pix_fmt yuv420p", preset);
        audio_codec = "aac";
    }

    const char *extra_audio = smooth_mode ? "" : "-b:a 128k -ar 44100";
    char af[512];
    int n;

    if ((enable_pitch || enable_special_pitch) &&
         has_rubberband && has_audio) {
        snprintf(af, sizeof(af),
            "rubberband=tempo=%.6f:pitch=%.6f:pitchq=speed,"
            "volume=%.2fdB",
            tempo, pitch_ratio, volume_adjustment);
        n = snprintf(cmd_out, cmd_sz,
            "ffmpeg -threads 0 -i \"%s\""
            " -filter_complex"
            " \"[0:v]setpts=%.6f*PTS%s[v];[0:a]%s[a]\""
            " -map \"[v]\" -map \"[a]\""
            " -c:v %s %s -r %.4f -c:a %s %s"
            " -shortest -y \"%s\"",
            input_path, video_pts, scale_filter, af,
            video_codec, video_params, effective_fps,
            audio_codec, extra_audio, output_path);

    } else if ((enable_pitch || enable_special_pitch) &&
               !has_rubberband && has_audio) {
        int pitched_rate = (int)(44100.0 * pitch_ratio);
        snprintf(af, sizeof(af),
            "atempo=%.6f,asetrate=%d,aresample=44100,"
            "volume=%.2fdB",
            tempo, pitched_rate, volume_adjustment);
        n = snprintf(cmd_out, cmd_sz,
            "ffmpeg -threads 0 -i \"%s\""
            " -filter_complex"
            " \"[0:v]setpts=%.6f*PTS%s[v];[0:a]%s[a]\""
            " -map \"[v]\" -map \"[a]\""
            " -c:v %s %s -r %.4f -c:a %s %s"
            " -shortest -y \"%s\"",
            input_path, video_pts, scale_filter, af,
            video_codec, video_params, effective_fps,
            audio_codec, extra_audio, output_path);

    } else if ((enable_pitch || enable_special_pitch) && !has_audio) {
        n = snprintf(cmd_out, cmd_sz,
            "ffmpeg -threads 0 -i \"%s\""
            " -vf \"setpts=%.6f*PTS%s\""
            " -c:v %s %s -r %.4f -an -y \"%s\"",
            input_path, video_pts, scale_filter,
            video_codec, video_params, effective_fps,
            output_path);

    } else if (has_rubberband && has_audio) {
        snprintf(af, sizeof(af),
            "rubberband=tempo=%.6f:pitchq=speed,volume=%.2fdB",
            tempo, volume_adjustment);
        n = snprintf(cmd_out, cmd_sz,
            "ffmpeg -threads 0 -i \"%s\""
            " -filter_complex"
            " \"[0:v]setpts=%.6f*PTS%s[v];[0:a]%s[a]\""
            " -map \"[v]\" -map \"[a]\""
            " -c:v %s %s -r %.4f -c:a %s %s"
            " -shortest -y \"%s\"",
            input_path, video_pts, scale_filter, af,
            video_codec, video_params, effective_fps,
            audio_codec, extra_audio, output_path);

    } else if (has_audio) {
        snprintf(af, sizeof(af),
            "atempo=%.6f,volume=%.2fdB",
            tempo, volume_adjustment);
        n = snprintf(cmd_out, cmd_sz,
            "ffmpeg -threads 0 -i \"%s\""
            " -filter_complex"
            " \"[0:v]setpts=%.6f*PTS%s[v];[0:a]%s[a]\""
            " -map \"[v]\" -map \"[a]\""
            " -c:v %s %s -r %.4f -c:a %s %s"
            " -shortest -y \"%s\"",
            input_path, video_pts, scale_filter, af,
            video_codec, video_params, effective_fps,
            audio_codec, extra_audio, output_path);

    } else {
        n = snprintf(cmd_out, cmd_sz,
            "ffmpeg -threads 0 -i \"%s\""
            " -vf \"setpts=%.6f*PTS%s\""
            " -c:v %s %s -r %.4f -an -y \"%s\"",
            input_path, video_pts, scale_filter,
            video_codec, video_params, effective_fps,
            output_path);
    }

    return (n >= 0 && (size_t)n < cmd_sz) ? 0 : -1;
}

/* ─────────────────────────────────────────────────────────────────
 * Core processing function
 * ───────────────────────────────────────────────────────────────── */
static int process_video_cumulative(
        const char *input_path, const char *output_path,
        int export_num, int iteration, double reference_size_mb,
        int enable_pitch, int enable_special_pitch,
        int has_rubberband, int has_loudnorm,
        double target_volume_db, double effective_fps,
        double original_video_duration,
        int silent, int text_size, int enable_color_mode,
        const char *preset, int smooth_mode, int target_height)
{
    (void)has_loudnorm; /* reserved for future loudnorm support */

    /* ── Temp file paths ── */
    char temp_dir[MAX_PATH_LEN];
    snprintf(temp_dir, sizeof(temp_dir), "%s", output_path);
    char *last_slash = strrchr(temp_dir, '/');
    if (last_slash) *last_slash = '\0';
    else snprintf(temp_dir, sizeof(temp_dir), ".");

    const char *ext = get_file_extension(smooth_mode);
    char temp_sped[MAX_PATH_LEN],
         temp_list[MAX_PATH_LEN],
         temp_concat[MAX_PATH_LEN];
    snprintf(temp_sped, sizeof(temp_sped),
             "%s/temp_sped_%d_%d%s",   temp_dir, export_num, (int)getpid(), ext);
    snprintf(temp_list, sizeof(temp_list),
             "%s/temp_list_%d_%d.txt", temp_dir, export_num, (int)getpid());
    snprintf(temp_concat, sizeof(temp_concat),
             "%s/temp_concat_%d_%d%s", temp_dir, export_num, (int)getpid(), ext);

    int success = 0;

    /* ── Power text ── */
    char power_text[128];
    {
        long long ep = 1LL << export_num;
        format_power_notation(ep, power_text, sizeof(power_text));
    }
    if (!silent)
        printf("  Export power (2^%d): %s\n", export_num, power_text);

    char text_string[256];
    snprintf(text_string, sizeof(text_string),
             "%d - %s", export_num, power_text);
    char text_escaped[512];
    escape_text_for_ffmpeg(text_string, text_escaped, sizeof(text_escaped));

    /* ── Video info ── */
    VideoInfo info;
    get_video_info(input_path, &info);
    double input_duration = info.duration;
    if (input_duration <= 0)
        input_duration = ffprobe_get_duration(input_path);
    int has_audio = info.has_audio;

    double current_volume =
        has_audio ? get_audio_volume(input_path) : -20.0;
    double volume_adjustment = target_volume_db - current_volume;

    /* ── Pitch / speed parameters ── */
    double pitch_ratio = 1.0;
    char   pitch_semitones[8] = "0";
    char   pitch_mode_name[64] = "NON-PITCH";
    double tempo, video_pts;

    if (enable_special_pitch) {
        get_special_pitch_for_iteration(iteration, &pitch_ratio,
                                        pitch_semitones,
                                        sizeof(pitch_semitones));
        snprintf(pitch_mode_name, sizeof(pitch_mode_name),
                 "SPECIAL PITCH (%s semitones)", pitch_semitones);
    } else if (enable_pitch) {
        pitch_ratio = FIXED_PITCH_RATIO;
        snprintf(pitch_semitones, sizeof(pitch_semitones), "+1");
        snprintf(pitch_mode_name, sizeof(pitch_mode_name),
                 "PITCH (+1 semitone)");
    }

    /* ═══════════════════════════════════════════
     * STEP 1 – Speed-up
     * ═══════════════════════════════════════════ */
    if (enable_pitch || enable_special_pitch) {
        double target_sped_duration = original_video_duration / 2.0;
        if (!silent) {
            printf("  Mode: %s (with duration correction)\n",
                   pitch_mode_name);
            if (smooth_mode)
                printf("  Smooth Mode: ENABLED\n");
            if (target_height)
                printf("  Quality: %dp\n", target_height);
            printf("  Pitch ratio: %.6f (%s semitones)\n",
                   pitch_ratio, pitch_semitones);
            printf("  Original video duration: %.6fs (TARGET)\n",
                   original_video_duration);
            printf("  Current input duration: %.6fs\n", input_duration);
            printf("  Target after speedup: %.6fs\n",
                   target_sped_duration);
            printf("  Volume: %.1fdB -> %.1fdB (adjust: %+.1fdB)\n",
                   current_volume, target_volume_db, volume_adjustment);
            printf("  Step 1/3: Speedup with duration correction...\n");
        }

        tempo     = input_duration / target_sped_duration;
        video_pts = target_sped_duration / input_duration;

        double frame_duration   = 1.0 / effective_fps;
        double precision_thresh =
            (0.001 < frame_duration / 2.0) ? 0.001 : frame_duration / 2.0;
        double best_tempo     = tempo;
        double best_video_pts = video_pts;
        double best_error     = 1e18;
        double best_duration  = 0.0;

        char cmd[MAX_CMD_LEN];
        char full_cmd[MAX_CMD_LEN + 32];
        char verify_msg[256];

        for (int attempt = 0; attempt < MAX_SPEED_RETRIES; attempt++) {
            if (!silent)
                printf("    Attempt %d: tempo=%.6f, video_pts=%.6f\n",
                       attempt + 1, tempo, video_pts);

            if (file_exists(temp_sped)) remove(temp_sped);

            if (build_speedup_command(cmd, sizeof(cmd),
                    input_path, temp_sped,
                    tempo, video_pts,
                    enable_pitch, enable_special_pitch,
                    pitch_ratio, has_rubberband, has_audio,
                    volume_adjustment, effective_fps,
                    preset, smooth_mode, target_height) != 0) {
                fprintf(stderr, "  ❌ Command buffer overflow\n");
                goto cleanup;
            }
            snprintf(full_cmd, sizeof(full_cmd),
                     "%s 2>/dev/null", cmd);
            if (run_cmd(full_cmd) != 0) {
                fprintf(stderr,
                        "  ❌ Speed-up failed (attempt %d)\n",
                        attempt + 1);
                goto cleanup;
            }
            if (!verify_output_file(temp_sped,
                                    verify_msg, sizeof(verify_msg))) {
                fprintf(stderr,
                        "  ❌ Speed-up invalid: %s\n", verify_msg);
                goto cleanup;
            }

            double actual = ffprobe_get_duration(temp_sped);
            double err    = fabs(actual - target_sped_duration);
            if (!silent)
                printf("      Result: %.6fs (error: %.6fs)\n",
                       actual, err);

            if (err < best_error) {
                best_error     = err;
                best_tempo     = tempo;
                best_video_pts = video_pts;
                best_duration  = actual;
            }
            if (err <= precision_thresh) {
                if (!silent)
                    printf("      %s✓%s Perfect match!\n",
                           CLR_GRN, CLR_RST);
                break;
            }
            if (attempt >= 3 && err >= best_error) {
                if (!silent)
                    printf("      %s⚠%s Not improving, using best"
                           " (error: %.6fs)\n",
                           CLR_YLW, CLR_RST, best_error);
                if (best_duration != actual) {
                    tempo     = best_tempo;
                    video_pts = best_video_pts;
                    if (file_exists(temp_sped)) remove(temp_sped);
                    build_speedup_command(cmd, sizeof(cmd),
                        input_path, temp_sped,
                        tempo, video_pts,
                        enable_pitch, enable_special_pitch,
                        pitch_ratio, has_rubberband, has_audio,
                        volume_adjustment, effective_fps,
                        preset, smooth_mode, target_height);
                    snprintf(full_cmd, sizeof(full_cmd),
                             "%s 2>/dev/null", cmd);
                    run_cmd(full_cmd);
                }
                break;
            }
            double corr = actual / target_sped_duration;
            tempo     = fmax(0.5, fmin(tempo * corr, 100.0));
            video_pts = fmax(0.01, fmin(1.0 / tempo, 2.0));
        }

        double final_sped = ffprobe_get_duration(temp_sped);
        if (!silent)
            printf("    Final: %.6fs (tempo: %.6f)\n",
                   final_sped, tempo);

    } else {
        /* Standard 2x */
        tempo = 2.0; video_pts = 0.5;
        if (!silent) {
            printf("  Mode: NON-PITCH (standard 2x speed)\n");
            if (smooth_mode)  printf("  Smooth Mode: ENABLED\n");
            if (target_height) printf("  Quality: %dp\n", target_height);
            printf("  Input duration: %.6fs\n", input_duration);
            printf("  Volume: %.1fdB -> %.1fdB (adjust: %+.1fdB)\n",
                   current_volume, target_volume_db, volume_adjustment);
            printf("  Step 1/3: Standard 2x speedup...\n");
        }
        if (file_exists(temp_sped)) remove(temp_sped);

        char cmd[MAX_CMD_LEN], full_cmd[MAX_CMD_LEN + 32];
        if (build_speedup_command(cmd, sizeof(cmd),
                input_path, temp_sped,
                tempo, video_pts,
                enable_pitch, enable_special_pitch,
                pitch_ratio, has_rubberband, has_audio,
                volume_adjustment, effective_fps,
                preset, smooth_mode, target_height) != 0) {
            fprintf(stderr, "  ❌ Command buffer overflow\n");
            goto cleanup;
        }
        snprintf(full_cmd, sizeof(full_cmd), "%s 2>/dev/null", cmd);
        if (run_cmd(full_cmd) != 0) {
            fprintf(stderr, "  ❌ Speed-up failed\n");
            goto cleanup;
        }
        char vmsg[256];
        if (!verify_output_file(temp_sped, vmsg, sizeof(vmsg))) {
            fprintf(stderr, "  ❌ Speed-up invalid: %s\n", vmsg);
            goto cleanup;
        }
        double actual = ffprobe_get_duration(temp_sped);
        if (!silent) {
            double sr = (actual > 0) ? input_duration / actual : 0;
            printf("    Result: %.6fs (speed ratio: %.2fx)\n",
                   actual, sr);
        }
    }

    /* ═══════════════════════════════════════════
     * STEP 2 – Duplicate (concat with itself)
     * ═══════════════════════════════════════════ */
    if (!silent) printf("  Step 2/3: Duplicating video...\n");
    {
        char abs_sped[MAX_PATH_LEN];
        if (!realpath(temp_sped, abs_sped))
            snprintf(abs_sped, sizeof(abs_sped), "%s", temp_sped);

        FILE *lf = fopen(temp_list, "w");
        if (!lf) {
            fprintf(stderr, "  ❌ Cannot write temp list\n");
            goto cleanup;
        }
        fprintf(lf, "file '%s'\nfile '%s'\n", abs_sped, abs_sped);
        fclose(lf);

        char cmd_concat[MAX_CMD_LEN];
        snprintf(cmd_concat, sizeof(cmd_concat),
            "ffmpeg -threads 0 -f concat -safe 0 -i \"%s\""
            " -c copy -y \"%s\" 2>/dev/null",
            temp_list, temp_concat);
        if (run_cmd(cmd_concat) != 0) {
            fprintf(stderr, "  ❌ Concat failed\n");
            goto cleanup;
        }
        char vmsg[256];
        if (!verify_output_file(temp_concat, vmsg, sizeof(vmsg))) {
            fprintf(stderr, "  ❌ Concat invalid: %s\n", vmsg);
            goto cleanup;
        }
        double concat_dur = ffprobe_get_duration(temp_concat);
        if (!silent) {
            if (enable_pitch || enable_special_pitch)
                printf("    Concatenated: %.6fs (target: %.6fs)\n",
                       concat_dur, original_video_duration);
            else
                printf("    Concatenated: %.6fs\n", concat_dur);
        }
    }

    /* ═══════════════════════════════════════════
     * STEP 3 – Text overlay + final encode
     * ═══════════════════════════════════════════ */
    if (!silent) printf("  Step 3/3: Adding text and exporting...\n");
    {
        double concat_dur = ffprobe_get_duration(temp_concat);

        /* Bitrate */
        double target_size_mb        = reference_size_mb * 1.15;
        double target_bitrate_total  =
            (target_size_mb * 8.0 * 1024.0) / concat_dur;
        int audio_bitrate            = 128;
        int target_video_bitrate;

        if (target_height > 0 && !smooth_mode) {
            double orig_h =
                (info.height > 0) ? (double)info.height : 1080.0;
            double bscale =
                fmax(0.1, pow((double)target_height / orig_h, 1.5));
            target_video_bitrate =
                (int)fmax(200.0,
                    (target_bitrate_total - audio_bitrate) * bscale);
        } else {
            target_video_bitrate =
                (int)fmax(500.0, target_bitrate_total - audio_bitrate);
        }

        if (!silent) {
            if (!smooth_mode)
                printf("    Target: %d kbps\n", target_video_bitrate);
            else
                printf("    Target: Lossless (smooth mode)\n");
        }

        /* Drawtext filter */
        char drawtext[MAX_STR_LEN];
        snprintf(drawtext, sizeof(drawtext),
            "drawtext=text='%s':"
            "fontcolor=red:bordercolor=blue:borderw=3:"
            "fontsize=%d:"
            "box=1:boxcolor=black@0.12:boxborderw=8:"
            "x=20:y=h-th-20",
            text_escaped, text_size);

        char video_filter[MAX_STR_LEN + 64];
        if (enable_color_mode)
            snprintf(video_filter, sizeof(video_filter),
                     "%s,hue=h=25", drawtext);
        else
            snprintf(video_filter, sizeof(video_filter),
                     "%s", drawtext);

        CodecConfig configs[8];
        int n_configs =
            select_codec_configs(preset, smooth_mode, configs, 8);

        VideoInfo concat_info;
        get_video_info(temp_concat, &concat_info);

        for (int ci = 0; ci < n_configs && !success; ci++) {
            CodecConfig *cc = &configs[ci];
            if (!silent)
                printf("    Trying %s...\n", cc->name);

            /* Flatten extra params */
            char extra_params[512] = "";
            for (int pi = 0; pi < cc->n_params; pi++) {
                strncat(extra_params, cc->params[pi],
                        sizeof(extra_params) - strlen(extra_params) - 2);
                strncat(extra_params, " ",
                        sizeof(extra_params) - strlen(extra_params) - 1);
            }

            if (file_exists(output_path)) remove(output_path);

            char cmd[MAX_CMD_LEN];
            int n;
            if (smooth_mode) {
                const char *a_args =
                    concat_info.has_audio ? "-c:a pcm_s16le" : "-an";
                n = snprintf(cmd, sizeof(cmd),
                    "ffmpeg -threads 0 -i \"%s\""
                    " -vf \"%s\" -c:v %s %s"
                    " -r %.4f %s -y \"%s\" 2>/dev/null",
                    temp_concat, video_filter,
                    cc->codec, extra_params,
                    effective_fps, a_args, output_path);
            } else if (concat_info.has_audio) {
                n = snprintf(cmd, sizeof(cmd),
                    "ffmpeg -threads 0 -i \"%s\""
                    " -vf \"%s\" -c:v %s %s"
                    " -b:v %dk -maxrate %dk -bufsize %dk"
                    " -r %.4f -c:a aac -b:a 128k"
                    " -movflags +faststart"
                    " -max_muxing_queue_size 1024"
                    " -y \"%s\" 2>/dev/null",
                    temp_concat, video_filter,
                    cc->codec, extra_params,
                    target_video_bitrate,
                    (int)(target_video_bitrate * 1.5),
                    (int)(target_video_bitrate * 2),
                    effective_fps, output_path);
            } else {
                n = snprintf(cmd, sizeof(cmd),
                    "ffmpeg -threads 0 -i \"%s\""
                    " -vf \"%s\" -c:v %s %s"
                    " -b:v %dk -r %.4f -an"
                    " -movflags +faststart"
                    " -y \"%s\" 2>/dev/null",
                    temp_concat, video_filter,
                    cc->codec, extra_params,
                    target_video_bitrate,
                    effective_fps, output_path);
            }

            if (n < 0 || (size_t)n >= sizeof(cmd)) {
                fprintf(stderr, "    ✗ %s: command overflow\n",
                        cc->name);
                continue;
            }

            if (run_cmd(cmd) == 0) {
                char vmsg[256];
                if (verify_output_file(output_path,
                                       vmsg, sizeof(vmsg))) {
                    if (!silent) {
                        VideoInfo oi;
                        get_video_info(output_path, &oi);
                        printf("    %s✓%s %s: %.2f MB\n",
                               CLR_GRN, CLR_RST, cc->name,
                               oi.size / (1024.0 * 1024.0));
                    }
                    success = 1;
                } else {
                    if (!silent)
                        printf("    ✗ %s: %s\n", cc->name, vmsg);
                }
            } else {
                if (!silent)
                    printf("    ✗ %s: ffmpeg returned error\n",
                           cc->name);
            }
        }

        if (!success) {
            fprintf(stderr, "  ❌ All codecs failed\n");
            goto cleanup;
        }

        double final_dur = ffprobe_get_duration(output_path);
        if (!silent) {
            printf("%s✓%s Export %d completed\n",
                   CLR_GRN, CLR_RST, export_num);
            printf("    Final duration: %.6fs\n", final_dur);
            if (enable_pitch || enable_special_pitch)
                printf("    Duration error: %.6fs\n",
                       fabs(final_dur - original_video_duration));
            printf("    Cumulative speed: %lldx\n",
                   1LL << (iteration + 1));
        }
    }

cleanup:
    if (file_exists(temp_sped))   remove(temp_sped);
    if (file_exists(temp_list))   remove(temp_list);
    if (file_exists(temp_concat)) remove(temp_concat);
    return success;
}

/* ─────────────────────────────────────────────────────────────────
 * Compile exports into one video
 * ───────────────────────────────────────────────────────────────── */
static int compile_exports(const char **export_files, int n_files,
                           const char *exports_dir,
                           double effective_fps,
                           const char *preset,
                           int watermark_size, int smooth_mode)
{
    printf("\n============================================================\n");
    printf("COMPILING ALL EXPORTS...\n");
    printf("============================================================\n");

    char output_path[MAX_PATH_LEN], output_name[MAX_PATH_LEN];
    get_unique_filename(exports_dir, "SpeedExp-Compilation",
                        smooth_mode,
                        output_path, sizeof(output_path),
                        output_name, sizeof(output_name));
    const char *ext = get_file_extension(smooth_mode);
    printf("  Output: %s%s\n", output_name, ext);
    printf("  Merging %d exports...\n", n_files);

    char temp_list[MAX_PATH_LEN], temp_concat[MAX_PATH_LEN];
    snprintf(temp_list, sizeof(temp_list),
             "%s/temp_compile_list_%d.txt",
             exports_dir, (int)getpid());
    snprintf(temp_concat, sizeof(temp_concat),
             "%s/temp_compile_concat_%d%s",
             exports_dir, (int)getpid(), ext);

    FILE *lf = fopen(temp_list, "w");
    if (!lf) {
        fprintf(stderr, "❌ Cannot write list\n");
        return 0;
    }
    for (int i = 0; i < n_files; i++) {
        char abs[MAX_PATH_LEN];
        if (!realpath(export_files[i], abs))
            snprintf(abs, sizeof(abs), "%s", export_files[i]);
        fprintf(lf, "file '%s'\n", abs);
    }
    fclose(lf);

    printf("  Step 1/2: Concatenating exports...\n");
    char cmd[MAX_CMD_LEN];
    snprintf(cmd, sizeof(cmd),
        "ffmpeg -threads 0 -f concat -safe 0 -i \"%s\""
        " -c copy -y \"%s\" 2>/dev/null",
        temp_list, temp_concat);
    if (run_cmd(cmd) != 0) {
        fprintf(stderr, "  ❌ Concat failed\n");
        remove(temp_list);
        return 0;
    }
    char vmsg[256];
    if (!verify_output_file(temp_concat, vmsg, sizeof(vmsg))) {
        fprintf(stderr, "  ❌ Concat invalid: %s\n", vmsg);
        remove(temp_list);
        return 0;
    }
    VideoInfo ci;
    get_video_info(temp_concat, &ci);
    printf("    Total duration: %.2fs\n", ci.duration);
    printf("  Step 2/2: Adding watermark...\n");

    char wm_escaped[512];
    escape_text_for_ffmpeg("Made with SpeedExp.c.",
                           wm_escaped, sizeof(wm_escaped));
    char wm_filter[MAX_STR_LEN];
    snprintf(wm_filter, sizeof(wm_filter),
        "drawtext=text='%s':"
        "fontcolor=white@0.75:bordercolor=black@0.75:borderw=2:"
        "fontsize=%d:"
        "box=1:boxcolor=orange@0.75:boxborderw=5:"
        "x=w-tw-20:y=20",
        wm_escaped, watermark_size);

    CodecConfig configs[8];
    int n_configs =
        select_codec_configs(preset, smooth_mode, configs, 8);
    int success = 0;

    for (int i = 0; i < n_configs && !success; i++) {
        CodecConfig *cc = &configs[i];
        printf("    Trying %s...\n", cc->name);

        char extra[512] = "";
        for (int pi = 0; pi < cc->n_params; pi++) {
            strncat(extra, cc->params[pi],
                    sizeof(extra) - strlen(extra) - 2);
            strncat(extra, " ",
                    sizeof(extra) - strlen(extra) - 1);
        }

        if (file_exists(output_path)) remove(output_path);

        if (smooth_mode)
            snprintf(cmd, sizeof(cmd),
                "ffmpeg -threads 0 -i \"%s\" -vf \"%s\""
                " -c:v %s %s -r %.4f"
                " -c:a pcm_s16le -y \"%s\" 2>/dev/null",
                temp_concat, wm_filter,
                cc->codec, extra, effective_fps, output_path);
        else
            snprintf(cmd, sizeof(cmd),
                "ffmpeg -threads 0 -i \"%s\" -vf \"%s\""
                " -c:v %s %s -r %.4f"
                " -c:a aac -b:a 128k"
                " -movflags +faststart"
                " -max_muxing_queue_size 1024"
                " -y \"%s\" 2>/dev/null",
                temp_concat, wm_filter,
                cc->codec, extra, effective_fps, output_path);

        if (run_cmd(cmd) == 0) {
            if (verify_output_file(output_path, vmsg, sizeof(vmsg))) {
                printf("    %s✓%s %s\n", CLR_GRN, CLR_RST, cc->name);
                success = 1;
            } else {
                printf("    ✗ %s: %s\n", cc->name, vmsg);
            }
        } else {
            printf("    ✗ %s: ffmpeg returned error\n", cc->name);
        }
    }

    remove(temp_list);
    remove(temp_concat);

    if (!success) {
        fprintf(stderr, "❌ Failed to add watermark\n");
        return 0;
    }

    VideoInfo oi;
    get_video_info(output_path, &oi);
    printf("\n%s✓%s COMPILATION COMPLETE!\n", CLR_GRN, CLR_RST);
    printf("  File: %s%s\n", output_name, ext);
    printf("  Size: %.2f MB\n", oi.size / (1024.0 * 1024.0));
    printf("  Duration: %.2fs\n", oi.duration);
    return 1;
}

/* ─────────────────────────────────────────────────────────────────
 * Compile-existing mode
 * ───────────────────────────────────────────────────────────────── */
static int compile_existing_exports_mode(const char *exports_dir,
                                         const char *preset,
                                         int smooth_mode)
{
    printf("\n============================================================\n");
    printf("COMPILE EXISTING EXPORTS MODE\n");
    printf("============================================================\n");

    ExportEntry entries[MAX_EXPORTS];
    int n = find_existing_exports(exports_dir, entries, MAX_EXPORTS);
    if (n == 0) {
        printf("\n❌ No existing export files found in Exports folder.\n");
        printf("   Location: %s\n", exports_dir);
        return 0;
    }

    printf("\n%s✓%s Found %d export file(s):\n\n",
           CLR_GRN, CLR_RST, n);
    double total_size = 0.0, total_dur = 0.0;
    for (int i = 0; i < n; i++) {
        VideoInfo vi;
        get_video_info(entries[i].path, &vi);
        double sz = vi.size / (1024.0 * 1024.0);
        total_size += sz;
        total_dur  += vi.duration;
        printf("  [%d] %s\n      Size: %.2f MB | Duration: %.2fs\n",
               entries[i].export_num, entries[i].filename,
               sz, vi.duration);
    }
    printf("\n  ────────────────────────────────────────\n");
    printf("  Total: %d files, %.2f MB, %.2fs\n\n",
           n, total_size, total_dur);

    VideoInfo sample;
    get_video_info(entries[0].path, &sample);
    double fps = (sample.fps > 0) ? sample.fps : 30.0;
    printf("  Detected FPS: %.2f\n", fps);

    printf("\nResize watermark to?: ");
    fflush(stdout);
    char buf[64];
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    int watermark_size =
        (str_isdigits(buf) && atoi(buf) > 0)
        ? atoi(buf) : DEFAULT_WATERMARK_SIZE;

    printf("\nProceed with compilation? (N/Y): ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    if (strcmp(buf, "Y") != 0) {
        printf("Compilation cancelled.\n");
        return 0;
    }

    const char *paths[MAX_EXPORTS];
    for (int i = 0; i < n; i++) paths[i] = entries[i].path;
    compile_exports(paths, n, exports_dir,
                    fps, preset, watermark_size, smooth_mode);
    return 1;
}

/* ─────────────────────────────────────────────────────────────────
 * User inputs
 * ───────────────────────────────────────────────────────────────── */
typedef struct {
    char   video_path[MAX_PATH_LEN];
    int    num_exports;
    int    start_num;
    int    enable_pitch;
    int    enable_special_pitch;
    int    text_size;
    int    enable_color_mode;
    char   preset[32];
    int    watermark_size;
    double custom_fps;      /* 0 = use original */
    int    target_height;   /* 0 = original */
} UserInputs;

static void get_user_inputs(int use_editor_selection, UserInputs *ui)
{
    char buf[256];

    /* Video path */
    if (use_editor_selection) {
        if (!select_video_from_movies(ui->video_path,
                                      sizeof(ui->video_path))) {
            fprintf(stderr, "❌ Could not select video\n");
            exit(1);
        }
    } else {
        printf("Video File Location?: ");
        fflush(stdout);
        fgets(buf, sizeof(buf), stdin);
        str_trim(buf);
        /* strip surrounding quotes */
        if ((buf[0] == '"' || buf[0] == '\'') && strlen(buf) > 1) {
            size_t l = strlen(buf);
            if (buf[l - 1] == '"' || buf[l - 1] == '\'')
                buf[l - 1] = '\0';
            memmove(buf, buf + 1, strlen(buf));
        }
        if (!buf[0]) {
            fprintf(stderr, "❌ Empty path\n");
            exit(1);
        }
        char err[256];
        if (!validate_video_file(buf, err, sizeof(err))) {
            fprintf(stderr, "❌ %s\n", err);
            exit(1);
        }
        snprintf(ui->video_path, sizeof(ui->video_path), "%s", buf);
    }

    /* Number of exports */
    printf("How much exports?: ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    if (!str_isdigits(buf) || atoi(buf) <= 0) {
        fprintf(stderr, "❌ Invalid export count\n");
        exit(1);
    }
    ui->num_exports = atoi(buf);
    if (ui->num_exports > 20) {
        printf("Warning: %d exports may take long. Continue? (y/n): ",
               ui->num_exports);
        fflush(stdout);
        fgets(buf, sizeof(buf), stdin);
        str_trim(buf);
        if (tolower((unsigned char)buf[0]) != 'y') {
            printf("Cancelled\n");
            exit(0);
        }
    }

    /* Starting number */
    printf("Starting Number?: ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    if (!str_isdigits(buf)) {
        fprintf(stderr, "❌ Invalid starting number\n");
        exit(1);
    }
    ui->start_num = atoi(buf);

    /* Special pitch */
    printf("Use special pitches? (N/Y)?: ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    if (strcmp(buf, "Y") == 0) {
        ui->enable_special_pitch = 1;
        ui->enable_pitch         = 1;
        printf("  ✓ Special pitches enabled: +7st, -5st, +7st, -5st, ...\n");
    } else {
        ui->enable_special_pitch = 0;
        printf("Set Pitch Increase (N/Y)?: ");
        fflush(stdout);
        fgets(buf, sizeof(buf), stdin);
        str_trim(buf);
        str_toupper(buf);
        ui->enable_pitch = (strcmp(buf, "Y") == 0);
    }

    /* Text size */
    printf("Change text size to num?: ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    if (buf[0] && str_isdigits(buf) && atoi(buf) > 0)
        ui->text_size = atoi(buf);
    else {
        if (buf[0]) printf("  Error!: invalid size.\n");
        ui->text_size = DEFAULT_TEXT_SIZE;
    }

    /* Watermark size */
    printf("Resize watermark to?: ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    if (buf[0] && str_isdigits(buf) && atoi(buf) > 0)
        ui->watermark_size = atoi(buf);
    else
        ui->watermark_size = DEFAULT_WATERMARK_SIZE;

    /* Color mode */
    printf("Use Color Mode (N/Y)?: ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    ui->enable_color_mode = (strcmp(buf, "Y") == 0);

    /* FPS */
    printf("Set FPS? (empty to keep original): ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    ui->custom_fps = 0.0;
    if (buf[0]) {
        double f = atof(buf);
        if (f > 0 && f <= 240) {
            ui->custom_fps = f;
            printf("  ✓ FPS set to %.2f\n", f);
        } else {
            printf("  Error!: FPS out of range. Using original.\n");
        }
    }

    /* Quality */
    printf("\n  Set quality?\n");
    printf("    [1] 144p\n");
    printf("    [2] 360p\n");
    printf("    [3] 480p\n");
    printf("    [4] 720p\n");
    printf("    [5] 1080p\n");
    printf("    [empty] Original\n");
    printf("  Select (1-5, empty for original): ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    ui->target_height = 0;
    if (buf[0] && str_isdigits(buf)) {
        int q = atoi(buf);
        if (q >= 1 && q <= N_QUALITY_OPTIONS) {
            ui->target_height = QUALITY_OPTIONS[q - 1].height;
            printf("  ✓ Quality set to %s\n",
                   QUALITY_OPTIONS[q - 1].label);
        } else {
            printf("  Invalid selection, using original quality.\n");
        }
    } else if (buf[0]) {
        printf("  Invalid selection, using original quality.\n");
    }

    /* Preset */
    printf("Use fast exports? (N/Y/Z/U)?: ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    if      (strcmp(buf, "Y") == 0)
        snprintf(ui->preset, sizeof(ui->preset), "veryfast");
    else if (strcmp(buf, "Z") == 0)
        snprintf(ui->preset, sizeof(ui->preset), "superfast");
    else if (strcmp(buf, "U") == 0)
        snprintf(ui->preset, sizeof(ui->preset), "ultrafast");
    else if (strcmp(buf, "N") == 0)
        snprintf(ui->preset, sizeof(ui->preset), "fast");
    else {
        printf("  Invalid input, defaulting to fast\n");
        snprintf(ui->preset, sizeof(ui->preset), "fast");
    }
}

/* ─────────────────────────────────────────────────────────────────
 * main()
 * ───────────────────────────────────────────────────────────────── */
int main(void)
{
    if (!isatty(fileno(stdout))) g_use_color = 0;

    printf("=== SpeedExp.c - a program for helping speedy collabs ===\n");
    printf("Checking dependencies...\n");

    int has_rubberband = 0, has_loudnorm = 0;
    check_dependencies(&has_rubberband, &has_loudnorm);

    char ffmpeg_ver[256];
    get_ffmpeg_version(ffmpeg_ver, sizeof(ffmpeg_ver));
    printf("  FFmpeg: %s\n\n", ffmpeg_ver);

    const char *termux_dl =
        "/data/data/com.termux/files/home/storage/downloads";
    if (is_directory(termux_dl)) {
        printf("%s✓%s Termux detected\n", CLR_GRN, CLR_RST);
        printf("  Output: %s/Exports\n\n", termux_dl);
    } else {
        printf("%s⚠%s Using current directory\n\n", CLR_YLW, CLR_RST);
    }

    char exports_dir[MAX_PATH_LEN];
    create_exports_folder(exports_dir, sizeof(exports_dir));

    char buf[256];

    /* ── Smooth mode ── */
    printf("\nEnable smooth mode? (N/Y): ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    int smooth_mode = (strcmp(buf, "Y") == 0);
    if (smooth_mode)
        printf("  ✓ Smooth mode enabled"
               " (libx264 lossless + pcm_s16le, .mov)\n");
    else if (buf[0] != 'N')
        printf("  Invalid input, defaulting to N\n");

    /* ── Compile existing? ── */
    printf("\nCompile Existing export files? (N/Y): ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    if (strcmp(buf, "Y") == 0) {
        printf("Use fast exports? (N/Y/Z/U)?: ");
        fflush(stdout);
        fgets(buf, sizeof(buf), stdin);
        str_trim(buf);
        str_toupper(buf);
        const char *pr =
            (strcmp(buf, "Y") == 0) ? "veryfast" :
            (strcmp(buf, "Z") == 0) ? "superfast" :
            (strcmp(buf, "U") == 0) ? "ultrafast" : "fast";
        if (compile_existing_exports_mode(exports_dir, pr, smooth_mode)) {
            printf("\n============================================================\n");
            printf("%s✓%s ALL DONE!\n", CLR_GRN, CLR_RST);
            printf("============================================================\n");
            return 0;
        }
        printf("\nContinuing with normal export process...\n\n");
    } else if (buf[0] != 'N') {
        printf("  Invalid input, continuing...\n\n");
    }

    /* ── Editor selection ── */
    int use_editor_selection = 0;
    printf("\nSelect from video editor folders? (N/Y): ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    if (strcmp(buf, "Y") == 0) {
        char *mp = get_movies_path();
        if (mp) {
            DirEntry dirs[MAX_DIRS];
            int nd = list_subdirs(mp, dirs, MAX_DIRS);
            if (nd > 0) use_editor_selection = 1;
            else printf("  ❌ No video editor folders found!"
                        " Falling back to manual input...\n\n");
        } else {
            printf("  ❌ No video editor folders found!"
                   " Falling back to manual input...\n\n");
        }
    } else if (buf[0] != 'N') {
        printf("  Invalid input, using manual input...\n\n");
    }

    /* ── Collect user inputs ── */
    UserInputs ui;
    memset(&ui, 0, sizeof(ui));
    get_user_inputs(use_editor_selection, &ui);

    /* ── Initial video info ── */
    VideoInfo initial_info;
    get_video_info(ui.video_path, &initial_info);
    double initial_size = initial_info.size / (1024.0 * 1024.0);
    double original_video_duration = initial_info.duration;
    if (original_video_duration <= 0)
        original_video_duration = ffprobe_get_duration(ui.video_path);
    double original_fps  =
        (initial_info.fps > 0) ? initial_info.fps : 30.0;
    double effective_fps =
        (ui.custom_fps > 0) ? ui.custom_fps : original_fps;
    double target_volume_db =
        initial_info.has_audio
        ? get_audio_volume(ui.video_path)
        : TARGET_VOLUME_DEFAULT;

    const char *ext = get_file_extension(smooth_mode);

    /* Upscale warning */
    if (ui.target_height > 0 &&
        initial_info.height > 0 &&
        ui.target_height > initial_info.height) {
        printf("\n  %s⚠%s Warning: Upscaling from %dp to %dp"
               " (may reduce quality)\n",
               CLR_YLW, CLR_RST,
               initial_info.height, ui.target_height);
    }

    /* Configuration summary */
    printf("\nConfiguration:\n");
    printf("  Video:          %s\n",    ui.video_path);
    printf("  Codec:          %s\n",    initial_info.video_codec);
    printf("  Size:           %.2f MB\n", initial_size);
    printf("  Duration:       %.6fs\n", original_video_duration);
    printf("  Original FPS:   %.2f\n",  original_fps);
    printf("  Effective FPS:  %.2f%s\n", effective_fps,
           ui.custom_fps > 0 ? " (custom)" : " (original)");
    printf("  Resolution:     %dx%d\n",
           initial_info.width, initial_info.height);
    if (ui.target_height > 0)
        printf("  Target Quality: %dp\n", ui.target_height);
    else
        printf("  Target Quality: Original\n");
    printf("  Has Audio:      %s\n",
           initial_info.has_audio ? "Yes" : "No");
    printf("  Target Volume:  %.1fdB\n", target_volume_db);
    printf("  Exports:        %d\n",    ui.num_exports);
    printf("  Starting #:     %d\n",    ui.start_num);
    printf("  Smooth Mode:    %s\n",
           smooth_mode ? "ENABLED (.mov)" : "DISABLED (.mp4)");
    if (ui.enable_special_pitch)
        printf("  Pitch Mode:     SPECIAL (+7st, -5st alternating)\n");
    else if (ui.enable_pitch)
        printf("  Pitch Mode:     NORMAL (+1 semitone per export)\n");
    else
        printf("  Pitch Mode:     NONE (standard 2x)\n");
    printf("  Text Size:      %d\n",    ui.text_size);
    printf("  Color Mode:     %s\n",
           ui.enable_color_mode ? "YES (hue +25)" : "NO");
    printf("  Preset:         %s\n",    ui.preset);
    printf("  Rubberband:     %s\n",
           has_rubberband ? "Available" : "NOT available (fallback)");
    printf("  Watermark sz:   %d\n",    ui.watermark_size);
    printf("  Processing:     CUMULATIVE\n");

    /* ── Export loop ── */
    char exported_files[MAX_EXPORTS][MAX_PATH_LEN];
    int  n_exported = 0;
    char current_input[MAX_PATH_LEN];
    snprintf(current_input, sizeof(current_input),
             "%s", ui.video_path);
    double reference_size = initial_size;

    printf("\nStarting export process...\n");
    printf("Flow: Original → Export 1 → ... → Export %d\n\n",
           ui.num_exports);

    for (int i = 0; i < ui.num_exports; i++) {
        int export_num = ui.start_num + i;

        char power_display[128];
        {
            long long ep = 1LL << export_num;
            format_power_notation(ep, power_display,
                                  sizeof(power_display));
        }

        char base_name[64];
        snprintf(base_name, sizeof(base_name),
                 "export-%d", export_num);
        char output_path[MAX_PATH_LEN], actual_name[MAX_PATH_LEN];
        get_unique_filename(exports_dir, base_name, smooth_mode,
                            output_path, sizeof(output_path),
                            actual_name, sizeof(actual_name));

        /* Pitch info string */
        char pitch_info[64];
        if (ui.enable_special_pitch) {
            char ps[8]; double pr;
            get_special_pitch_for_iteration(i, &pr,
                                            ps, sizeof(ps));
            snprintf(pitch_info, sizeof(pitch_info),
                     "Special: %s semitones", ps);
        } else if (ui.enable_pitch) {
            snprintf(pitch_info, sizeof(pitch_info),
                     "+%d semitones total", i + 1);
        } else {
            snprintf(pitch_info, sizeof(pitch_info), "None");
        }

        printf("\n============================================================\n");
        printf("[Export %d/%d]\n", i + 1, ui.num_exports);
        printf("  Export Number: %d\n", export_num);
        printf("  Input:         %s\n", path_basename(current_input));
        printf("  Output:        %s%s\n", actual_name, ext);
        printf("  Text:          '%d - %s'\n",
               export_num, power_display);
        printf("  Pitch:         %s\n", pitch_info);
        if (ui.target_height > 0)
            printf("  Quality:       %dp\n", ui.target_height);
        printf("  FPS:           %.2f\n", effective_fps);
        if (ui.enable_color_mode)
            printf("  Color:         Hue +25\n");
        printf("============================================================\n");

        int ok = process_video_cumulative(
            current_input, output_path,
            export_num, i, reference_size,
            ui.enable_pitch, ui.enable_special_pitch,
            has_rubberband, has_loudnorm,
            target_volume_db, effective_fps,
            original_video_duration,
            /*silent=*/0, ui.text_size, ui.enable_color_mode,
            ui.preset, smooth_mode, ui.target_height);

        if (!ok) {
            fprintf(stderr,
                    "\n❌ Processing Error: Export %d failed\n",
                    export_num);
            exit(1);
        }

        snprintf(exported_files[n_exported++],
                 MAX_PATH_LEN, "%s", output_path);

        double out_size =
            file_size(output_path) / (1024.0 * 1024.0);
        double out_dur = ffprobe_get_duration(output_path);
        printf("  Size:    %.2f MB (%.1f%%)\n",
               out_size, (out_size / initial_size) * 100.0);
        if (ui.enable_pitch || ui.enable_special_pitch)
            printf("  Duration match: %.6fs (error: %.6fs)\n",
                   out_dur,
                   fabs(out_dur - original_video_duration));
        else
            printf("  Duration: %.2fs\n", out_dur);

        snprintf(current_input, sizeof(current_input),
                 "%s", output_path);
    }

    /* ── Summary ── */
    printf("\n============================================================\n");
    printf("%s✓%s ALL %d EXPORTS COMPLETED!\n",
           CLR_GRN, CLR_RST, ui.num_exports);
    printf("============================================================\n");
    printf("Location: %s\n\nExport Summary:\n", exports_dir);
    printf("============================================================\n");

    for (int i = 0; i < n_exported; i++) {
        int export_num = ui.start_num + i;
        double sz  = file_size(exported_files[i]) / (1024.0 * 1024.0);
        double dur = ffprobe_get_duration(exported_files[i]);

        char pow_display[128];
        {
            long long ep = 1LL << export_num;
            format_power_notation(ep, pow_display, sizeof(pow_display));
        }

        char pitch_display[64];
        if (ui.enable_special_pitch) {
            char ps[8]; double pr;
            get_special_pitch_for_iteration(i, &pr, ps, sizeof(ps));
            snprintf(pitch_display, sizeof(pitch_display),
                     "%sst (special)", ps);
        } else if (ui.enable_pitch) {
            snprintf(pitch_display, sizeof(pitch_display),
                     "+%dst", i + 1);
        } else {
            snprintf(pitch_display, sizeof(pitch_display), "None");
        }

        printf("\n  %s:\n", path_basename(exported_files[i]));
        printf("    Size:  %.2f MB (%.1f%%)\n",
               sz, (sz / initial_size) * 100.0);
        if (ui.enable_pitch || ui.enable_special_pitch)
            printf("    Duration: %.6fs (error: %.6fs)\n",
                   dur, fabs(dur - original_video_duration));
        else
            printf("    Duration: %.2fs\n", dur);
        printf("    Text:  '%d - %s'\n", export_num, pow_display);
        printf("    Speed: %lldx | Pitch: %s\n",
               1LL << (i + 1), pitch_display);
        if (ui.target_height > 0)
            printf("    Quality: %dp\n", ui.target_height);
        printf("    FPS:   %.2f\n", effective_fps);
    }
    printf("\n============================================================\n");

    /* ── Compile? ── */
    printf("\nCompile all exports into Video? (N/Y): ");
    fflush(stdout);
    fgets(buf, sizeof(buf), stdin);
    str_trim(buf);
    str_toupper(buf);
    if (strcmp(buf, "Y") == 0) {
        const char *ptrs[MAX_EXPORTS];
        for (int i = 0; i < n_exported; i++)
            ptrs[i] = exported_files[i];
        compile_exports(ptrs, n_exported, exports_dir,
                        effective_fps, ui.preset,
                        ui.watermark_size, smooth_mode);
    } else {
        printf("Skipping compilation.\n");
    }

    printf("\n============================================================\n");
    printf("%s✓%s ALL DONE!\n", CLR_GRN, CLR_RST);
    printf("============================================================\n");
    return 0;
}
