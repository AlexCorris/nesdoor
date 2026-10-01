#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdio.h>
#include "libretro_min.h"
#include "core.h"

struct nd_core {
 HMODULE dll; enum retro_pixel_format fmt; uint8_t *rgb; int w,h; uint16_t pad; double fps,sample_rate;
 int16_t *audio; size_t audio_n,audio_cap;
 void (__cdecl *init)(void); void (__cdecl *deinit)(void); void (__cdecl *run)(void); void (__cdecl *reset)(void);
 bool (__cdecl *load_game)(const struct retro_game_info*); void (__cdecl *unload_game)(void);
 void (__cdecl *get_av)(struct retro_system_av_info*);
 void (__cdecl *set_env)(retro_environment_t); void (__cdecl *set_video)(retro_video_refresh_t);
 void (__cdecl *set_as)(retro_audio_sample_t); void (__cdecl *set_ab)(retro_audio_sample_batch_t);
 void (__cdecl *set_poll)(retro_input_poll_t); void (__cdecl *set_state)(retro_input_state_t);
 size_t (__cdecl *serialize_size)(void); bool (__cdecl *serialize)(void*,size_t);
 bool (__cdecl *unserialize)(const void*,size_t); void *(__cdecl *get_mem_data)(unsigned);
 size_t (__cdecl *get_mem_size)(unsigned);
};
static struct nd_core *G; static char gerr[512];
const char *core_last_error(void){return gerr[0]?gerr:"Unknown libretro error";}
static void errwin(const char *what){DWORD e=GetLastError();char sys[256]={0};FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM|FORMAT_MESSAGE_IGNORE_INSERTS,NULL,e,0,sys,sizeof(sys),NULL);snprintf(gerr,sizeof(gerr),"%s failed (Win32 error %lu): %s",what,(unsigned long)e,sys);}
static bool envcb(unsigned cmd,void *data){if(cmd==RETRO_ENVIRONMENT_SET_PIXEL_FORMAT){G->fmt=*(enum retro_pixel_format*)data;return true;}if(cmd==RETRO_ENVIRONMENT_GET_CAN_DUPE){*(bool*)data=true;return true;}return false;}
static void video(const void *data,unsigned w,unsigned h,size_t pitch){
 unsigned x,y;const uint8_t*s=(const uint8_t*)data;size_t need=(size_t)w*h*3;if(!data)return;
 if(!G->rgb||G->w!=(int)w||G->h!=(int)h){uint8_t*p=(uint8_t*)realloc(G->rgb,need);if(!p)return;G->rgb=p;G->w=(int)w;G->h=(int)h;}
 for(y=0;y<h;y++)for(x=0;x<w;x++){uint8_t*d=G->rgb+3*((size_t)y*w+x);
  if(G->fmt==RETRO_PIXEL_FORMAT_XRGB8888){const uint8_t*q=s+y*pitch+x*4;d[0]=q[2];d[1]=q[1];d[2]=q[0];}
  else{const uint8_t*q=s+y*pitch+x*2;unsigned v=q[0]|(q[1]<<8);if(G->fmt==RETRO_PIXEL_FORMAT_RGB565){d[0]=((v>>11)&31)*255/31;d[1]=((v>>5)&63)*255/63;d[2]=(v&31)*255/31;}else{d[0]=((v>>10)&31)*255/31;d[1]=((v>>5)&31)*255/31;d[2]=(v&31)*255/31;}}}
}
static int aud_reserve(size_t add){size_t need=G->audio_n+add;if(need<=G->audio_cap)return 1;size_t cap=G->audio_cap?G->audio_cap:16384;while(cap<need)cap*=2;int16_t*p=(int16_t*)realloc(G->audio,cap*sizeof(int16_t));if(!p)return 0;G->audio=p;G->audio_cap=cap;return 1;}
static void asamp(int16_t l,int16_t r){if(aud_reserve(2)){G->audio[G->audio_n++]=l;G->audio[G->audio_n++]=r;}}
static size_t abatch(const int16_t*d,size_t n){size_t samples=n*2;if(aud_reserve(samples)){memcpy(G->audio+G->audio_n,d,samples*sizeof(int16_t));G->audio_n+=samples;}return n;}
static void poll(void){}
static int16_t state(unsigned port,unsigned dev,unsigned idx,unsigned id){(void)idx;if(port||dev!=RETRO_DEVICE_JOYPAD||id>15)return 0;return(G->pad&(1u<<id))?1:0;}
static FARPROC getsym(const char*n,int req){FARPROC p=GetProcAddress(G->dll,n);if(!p&&req)snprintf(gerr,sizeof(gerr),"Missing required libretro export: %s",n);return p;}
#define REQ(f,n) do{G->f=(void*)getsym(n,1);if(!G->f)goto fail;}while(0)
#define OPT(f,n) G->f=(void*)getsym(n,0)
nd_core *core_open(const char*dll,const char*rom){
 struct retro_game_info gi;struct retro_system_av_info av;gerr[0]=0;G=(struct nd_core*)calloc(1,sizeof(*G));if(!G){strcpy(gerr,"Out of memory");return NULL;}
 G->dll=LoadLibraryA(dll);if(!G->dll){errwin("LoadLibrary(fceumm_libretro.dll)");goto fail;}
 REQ(init,"retro_init");REQ(deinit,"retro_deinit");REQ(run,"retro_run");REQ(load_game,"retro_load_game");REQ(unload_game,"retro_unload_game");REQ(get_av,"retro_get_system_av_info");
 REQ(set_env,"retro_set_environment");REQ(set_video,"retro_set_video_refresh");REQ(set_as,"retro_set_audio_sample");REQ(set_ab,"retro_set_audio_sample_batch");REQ(set_poll,"retro_set_input_poll");REQ(set_state,"retro_set_input_state");
 OPT(reset,"retro_reset");OPT(serialize_size,"retro_serialize_size");OPT(serialize,"retro_serialize");OPT(unserialize,"retro_unserialize");OPT(get_mem_data,"retro_get_memory_data");OPT(get_mem_size,"retro_get_memory_size");
 G->set_env(envcb);G->set_video(video);G->set_as(asamp);G->set_ab(abatch);G->set_poll(poll);G->set_state(state);G->init();
 memset(&gi,0,sizeof(gi));gi.path=rom;if(!G->load_game(&gi)){snprintf(gerr,sizeof(gerr),"retro_load_game rejected ROM: %s",rom);G->deinit();goto fail;}
 memset(&av,0,sizeof(av));G->get_av(&av);G->fps=av.timing.fps;G->sample_rate=av.timing.sample_rate;if(G->fps<1||G->fps>1000)G->fps=60.0988;return G;
fail:if(G){if(G->dll)FreeLibrary(G->dll);free(G);}G=NULL;return NULL;
}
void core_close(nd_core*c){if(!c)return;c->unload_game();c->deinit();if(c->dll)FreeLibrary(c->dll);free(c->rgb);free(c->audio);free(c);G=NULL;}
int core_run(nd_core*c){if(!c)return 0;c->run();return 1;} const uint8_t*core_rgb(nd_core*c,int*w,int*h){if(!c||!c->rgb)return NULL;*w=c->w;*h=c->h;return c->rgb;}
void core_set_pad(nd_core*c,uint16_t b){if(c)c->pad=b;}double core_fps(nd_core*c){return c?c->fps:0;}double core_sample_rate(nd_core*c){return c?c->sample_rate:0;}
size_t core_take_audio(nd_core*c,int16_t*dst,size_t frames){size_t avail=c?c->audio_n/2:0;if(frames>avail)frames=avail;if(frames){memcpy(dst,c->audio,frames*2*sizeof(int16_t));memmove(c->audio,c->audio+frames*2,(c->audio_n-frames*2)*sizeof(int16_t));c->audio_n-=frames*2;}return frames;}
static int write_blob(const char*p,const void*d,size_t n){FILE*f=fopen(p,"wb");if(!f)return 0;int ok=fwrite(d,1,n,f)==n;fclose(f);return ok;}
static void*read_blob(const char*p,size_t*n){FILE*f=fopen(p,"rb");long z;void*b;if(!f)return NULL;fseek(f,0,SEEK_END);z=ftell(f);rewind(f);if(z<=0){fclose(f);return NULL;}b=malloc(z);if(!b){fclose(f);return NULL;}if(fread(b,1,z,f)!=(size_t)z){free(b);fclose(f);return NULL;}fclose(f);*n=z;return b;}
int core_save_state(nd_core*c,const char*p){if(!c||!c->serialize_size||!c->serialize)return 0;size_t n=c->serialize_size();void*b=malloc(n);int ok=0;if(b&&n&&c->serialize(b,n))ok=write_blob(p,b,n);free(b);return ok;}
int core_load_state(nd_core*c,const char*p){if(!c||!c->unserialize)return 0;size_t n;void*b=read_blob(p,&n);int ok=b?c->unserialize(b,n):0;free(b);return ok;}
int core_save_sram(nd_core*c,const char*p){if(!c||!c->get_mem_data||!c->get_mem_size)return 0;void*d=c->get_mem_data(0);size_t n=c->get_mem_size(0);return d&&n?write_blob(p,d,n):0;}
int core_load_sram(nd_core*c,const char*p){if(!c||!c->get_mem_data||!c->get_mem_size)return 0;void*d=c->get_mem_data(0);size_t cap=c->get_mem_size(0),n;void*b=read_blob(p,&n);if(!b||!d||!cap){free(b);return 0;}if(n>cap)n=cap;memcpy(d,b,n);free(b);return 1;}
void core_reset(nd_core*c){if(c&&c->reset)c->reset();}
