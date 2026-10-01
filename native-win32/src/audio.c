#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include "audio.h"
#include "door.h"
struct nd_audio{double src,gain,chunk;int rate,vol,on,seq;float*mono;int n,cap;};
static const char B64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static char*b64(const unsigned char*p,size_t n){size_t m=4*((n+2)/3),i,j=0;char*o=malloc(m+1);if(!o)return NULL;for(i=0;i<n;i+=3){uint32_t v=p[i]<<16;if(i+1<n)v|=p[i+1]<<8;if(i+2<n)v|=p[i+2];o[j++]=B64[(v>>18)&63];o[j++]=B64[(v>>12)&63];o[j++]=(i+1<n)?B64[(v>>6)&63]:'=';o[j++]=(i+2<n)?B64[v&63]:'=';}o[j]=0;return o;}
static void le16(unsigned char*p,unsigned v){p[0]=v;p[1]=v>>8;}static void le32(unsigned char*p,uint32_t v){p[0]=v;p[1]=v>>8;p[2]=v>>16;p[3]=v>>24;}
static void apc(const char*s){door_write("\x1b_",2);door_write(s,strlen(s));door_write("\x1b\\",2);}
nd_audio*audio_create(double src,int rate,int vol,double gain,double chunk){nd_audio*a=calloc(1,sizeof(*a));if(a){a->src=src;a->rate=rate;a->vol=vol;a->gain=gain;a->chunk=chunk;a->on=1;}return a;}
void audio_destroy(nd_audio*a){if(!a)return;audio_stop(a);free(a->mono);free(a);}
void audio_feed(nd_audio*a,const int16_t*s,int frames){int i;if(!a||!a->on||frames<=0)return;if(a->n+frames>a->cap){int c=a->cap?a->cap:16384;while(c<a->n+frames)c*=2;float*p=realloc(a->mono,c*sizeof(float));if(!p)return;a->mono=p;a->cap=c;}for(i=0;i<frames;i++)a->mono[a->n++]=((s[i*2]+s[i*2+1])/65536.0f)*(float)a->gain;}
static void sendchunk(nd_audio*a,const float*x,int n){
 int outn=(int)(n*a->rate/a->src);size_t wavsz=44+outn;unsigned char*w=malloc(wavsz);int i,slot=a->seq%8;char cmd[256],*enc;if(!w)return;
 memcpy(w,"RIFF",4);le32(w+4,(uint32_t)(wavsz-8));memcpy(w+8,"WAVEfmt ",8);le32(w+16,16);le16(w+20,1);le16(w+22,1);le32(w+24,a->rate);le32(w+28,a->rate);le16(w+32,1);le16(w+34,8);memcpy(w+36,"data",4);le32(w+40,outn);
 for(i=0;i<outn;i++){double pos=(double)i*a->src/a->rate;int k=(int)pos;if(k>=n)k=n-1;double v=x[k];if(v>1)v=1;if(v<-1)v=-1;w[44+i]=(unsigned char)((v*.95+1)*127.5);}
 enc=b64(w,wavsz);free(w);if(!enc)return;
 snprintf(cmd,sizeof(cmd),"SyncTERM:C;S;nesa%d.wav;",slot);door_write("\x1b_",2);door_write(cmd,strlen(cmd));door_write(enc,strlen(enc));door_write("\x1b\\",2);free(enc);
 snprintf(cmd,sizeof(cmd),"SyncTERM:A;Load;S=%d;nesa%d.wav",200+slot,slot);apc(cmd);
 snprintf(cmd,sizeof(cmd),"SyncTERM:A;Queue;C=2;S=%d;V=%d",200+slot,a->vol);apc(cmd);a->seq++;
}
void audio_pump(nd_audio*a){int need;if(!a||!a->on)return;need=(int)(a->chunk*a->src);while(a->n>=need){sendchunk(a,a->mono,need);memmove(a->mono,a->mono+need,(a->n-need)*sizeof(float));a->n-=need;}}
int audio_toggle(nd_audio*a){if(!a)return 0;a->on=!a->on;if(!a->on)audio_stop(a);return a->on;}
void audio_stop(nd_audio*a){if(!a)return;a->n=0;apc("SyncTERM:A;Flush;C=2");}
