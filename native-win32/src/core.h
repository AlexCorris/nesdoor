#ifndef NESDOOR_CORE_H
#define NESDOOR_CORE_H
#include <stdint.h>
#include <stddef.h>
typedef struct nd_core nd_core;
nd_core *core_open(const char *dll,const char *rom);
void core_close(nd_core*);
int core_run(nd_core*);
const uint8_t *core_rgb(nd_core*,int *w,int *h);
void core_set_pad(nd_core*,uint16_t bits);
double core_fps(nd_core*);
double core_sample_rate(nd_core*);
const char *core_last_error(void);
size_t core_take_audio(nd_core*, int16_t *dst, size_t stereo_frames);
int core_save_state(nd_core*, const char *path);
int core_load_state(nd_core*, const char *path);
int core_save_sram(nd_core*, const char *path);
int core_load_sram(nd_core*, const char *path);
void core_reset(nd_core*);
#endif
