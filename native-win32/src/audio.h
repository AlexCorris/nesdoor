#ifndef NESDOOR_AUDIO_H
#define NESDOOR_AUDIO_H
#include <stdint.h>
typedef struct nd_audio nd_audio;
nd_audio *audio_create(double src_rate,int out_rate,int volume,double gain,double chunk);
void audio_destroy(nd_audio*);
void audio_feed(nd_audio*,const int16_t *stereo,int frames);
void audio_pump(nd_audio*);
int audio_toggle(nd_audio*);
void audio_stop(nd_audio*);
#endif
