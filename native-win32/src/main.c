#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <ctype.h>
#include "door.h"
#include "core.h"
#include "sixel.h"
#include "audio.h"
#include "libretro_min.h"
#include <math.h>
#define MAX_ROMS 512
static void cleanup_log(const char *msg){
 FILE *f=fopen("nesdoor-c-exit.log","a");
 if(f){SYSTEMTIME t;GetLocalTime(&t);
 fprintf(f,"%02u:%02u:%02u.%03u %s\r\n",
 (unsigned)t.wHour,(unsigned)t.wMinute,(unsigned)t.wSecond,(unsigned)t.wMilliseconds,msg);
 fflush(f);fclose(f);}
}
static char roms[MAX_ROMS][MAX_PATH];static int nrom;static uint16_t pad;static int quit_game;static FILE*LOGF;
static void logmsg(const char*f,...){va_list a;SYSTEMTIME s;if(!LOGF)LOGF=fopen("nesdoor-c.log","a");if(!LOGF)return;GetLocalTime(&s);fprintf(LOGF,"%02u:%02u:%02u ",s.wHour,s.wMinute,s.wSecond);va_start(a,f);vfprintf(LOGF,f,a);va_end(a);fputc('\n',LOGF);fflush(LOGF);}
static void titleof(const char*fn,char*out,size_t n){size_t i,L;snprintf(out,n,"%s",fn);L=strlen(out);if(L>4&&!_stricmp(out+L-4,".nes"))out[L-4]=0;for(i=0;out[i];i++)if(out[i]=='_')out[i]=' ';}
static void scan(void){WIN32_FIND_DATAA f;HANDLE h=FindFirstFileA("roms\\*.nes",&f);nrom=0;if(h==INVALID_HANDLE_VALUE)return;do{if(!(f.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)&&nrom<MAX_ROMS)snprintf(roms[nrom++],MAX_PATH,"%s",f.cFileName);}while(FindNextFileA(h,&f));FindClose(h);}
#define KEYBUF_SIZE 256
static unsigned char keybuf[KEYBUF_SIZE];
static size_t keybuf_n=0;
static DWORD keybuf_esc_since=0;

static void key_consume(size_t n){
 if(n>=keybuf_n){keybuf_n=0;return;}
 memmove(keybuf,keybuf+n,keybuf_n-n);
 keybuf_n-=n;
}

static int key_decode(void){
 unsigned char c;

 if(!keybuf_n)return 0;
 c=keybuf[0];

 if(c!=27){
  key_consume(1);
  keybuf_esc_since=0;
  return c;
 }

 /*
  * A bare Escape may be either the Esc key or the beginning of an ANSI
  * sequence.  Keep it briefly so TCP fragmentation cannot turn ESC [ A
  * into three unrelated key events.
  */
 if(keybuf_n==1){
  if(!keybuf_esc_since)keybuf_esc_since=GetTickCount();
  if(GetTickCount()-keybuf_esc_since<80)return 0;
  key_consume(1);
  keybuf_esc_since=0;
  return 27;
 }

 if(keybuf[1]!='['){
  key_consume(1);
  keybuf_esc_since=0;
  return 27;
 }

 if(keybuf_n<3){
  if(!keybuf_esc_since)keybuf_esc_since=GetTickCount();
  if(GetTickCount()-keybuf_esc_since<80)return 0;
  key_consume(1);
  keybuf_esc_since=0;
  return 27;
 }

 keybuf_esc_since=0;

 switch(keybuf[2]){
  case 'A': key_consume(3); return 1001;
  case 'B': key_consume(3); return 1002;
  case 'C': key_consume(3); return 1003;
  case 'D': key_consume(3); return 1004;

  /* SyncTERM navigation sequences used by the Python client path. */
  case 'V': key_consume(3); return 1005; /* Page Up */
  case 'U': key_consume(3); return 1006; /* Page Down */
  case 'K': key_consume(3); return 1007; /* Home */

  case 'H': key_consume(3); return 1007;
  case 'F': key_consume(3); return 1008;

  /*
   * Standard CSI 5~ / 6~ Page Up/Page Down.  Wait for the final ~ so a
   * fragmented four-byte sequence is not consumed prematurely.
   */
  case '5':
  case '6':
   if(keybuf_n<4){
    if(!keybuf_esc_since)keybuf_esc_since=GetTickCount();
    return 0;
   }
   if(keybuf[3]=='~'){
    int k=(keybuf[2]=='5')?1005:1006;
    key_consume(4);
    return k;
   }
   break;
 }

 /*
  * Unknown CSI sequence: consume the complete short sequence when we can.
  * Otherwise consume ESC only rather than throwing away following input.
  */
 {
  size_t i;
  for(i=2;i<keybuf_n;i++){
   if(keybuf[i]>=0x40 && keybuf[i]<=0x7e){
    key_consume(i+1);
    return 0;
   }
  }
 }

 return 0;
}

static int key(int ms){
 DWORD start=GetTickCount();

 for(;;){
  int k=key_decode();
  if(k)return k;

  if(!door_connected())return -1;

  {
   unsigned char b[64];
   int wait=ms;
   int n;

   if(ms==0)wait=0;
   else{
    DWORD elapsed=GetTickCount()-start;
    if(elapsed>=(DWORD)ms){
     /*
      * Give a pending bare ESC one last chance to mature before
      * reporting a timeout.
      */
     k=key_decode();
     return k;
    }
    wait=ms-(int)elapsed;
    if(wait>20)wait=20;
   }

   n=door_read(b,sizeof(b),wait);
   if(n<0)return -1;

   if(n>0){
    size_t room=KEYBUF_SIZE-keybuf_n;
    size_t take=(size_t)n;

    if(take>room)take=room;

    if(take){
     memcpy(keybuf+keybuf_n,b,take);
     keybuf_n+=take;
    }

    continue;
   }
  }

  /*
   * key(0) must remain nonblocking for the emulation loop.
   * key_decode() above still preserves incomplete ANSI sequences.
   */
  if(ms==0)return key_decode();

  if(GetTickCount()-start>=(DWORD)ms)return key_decode();
 }
}
static void drain(void){int q=0;while(q<150){if(key(25)>0)q=0;else q+=25;}}
static int menu(void){int sel=0,k,i,top;char t[300];drain();
 if(nrom==0){
  door_printf("\x1b[0m\x1b[2J\x1b[H\x1b[?25l");
  door_printf("\x1b[8;25H\x1b[1;31mNO NES ROMS FOUND");
  door_printf("\x1b[10;15H\x1b[0;37mPlace .nes files in the roms directory.");
  door_printf("\x1b[12;21H\x1b[1;36mPress Q or Ctrl+Q to return.");
  for(;;){
   k=key(300);
   if(k=='q'||k=='Q'||k==17||k<0)return -1;
  }
 }
 for(;;){top=(sel/14)*14;door_printf("\x1b[0m\x1b[2J\x1b[H\x1b[?25l");door_printf("\x1b[3;29H\x1b[1;36mNeeds a sixel terminal such as SyncTERM");door_printf("\x1b[4;36H\x1b[1;32mNES ARCADE");door_printf("\x1b[6;4H\x1b[1;32m%d games",nrom);for(i=top;i<nrom&&i<top+14;i++){titleof(roms[i],t,sizeof(t));if(i==sel)door_printf("\x1b[%d;9H\x1b[44;1;37m%2d. %-58.58s\x1b[0m",8+i-top,i+1,t);else door_printf("\x1b[%d;11H\x1b[0;37m%2d. %s",8+i-top,i+1,t);}door_printf("\x1b[23;58H\x1b[1;33m%d of %d",sel+1,nrom);door_printf("\x1b[24;4H\x1b[1;36mUP/DOWN choose   PGUP/PGDN page   HOME/END   ENTER play");door_printf("\x1b[25;4H\x1b[1;36mCtrl+Q = leave");k=key(300);if(k<0)return -1;if(k==1001&&sel>0)sel--;else if(k==1002&&sel<nrom-1)sel++;else if(k==1005){sel-=14;if(sel<0)sel=0;}else if(k==1006){sel+=14;if(sel>=nrom)sel=nrom-1;}else if(k==1007)sel=0;else if(k==1008)sel=nrom-1;else if(k==13)return sel;else if(k==17||k=='q'||k=='Q')return-1;}}
static void pxrect(uint8_t *im,int W,int H,int x0,int y0,int x1,int y1,int r,int g,int b){
 int x,y;(void)r;
 if(x0<0)x0=0;if(y0<0)y0=0;if(x1>=W)x1=W-1;if(y1>=H)y1=H-1;
 for(y=y0;y<=y1;y++)for(x=x0;x<=x1;x++){uint8_t*q=im+3*((size_t)y*W+x);q[0]=r;q[1]=g;q[2]=b;}
}
static void pxcircle(uint8_t *im,int W,int H,int cx,int cy,int rr,int r,int g,int b){
 int x,y;for(y=cy-rr;y<=cy+rr;y++)for(x=cx-rr;x<=cx+rr;x++)
 if(x>=0&&y>=0&&x<W&&y<H&&((x-cx)*(x-cx)+(y-cy)*(y-cy)<=rr*rr)){
  uint8_t*q=im+3*((size_t)y*W+x);q[0]=r;q[1]=g;q[2]=b;
 }
}
static void rrect(uint8_t *im,int W,int H,int x0,int y0,int x1,int y1,int rr,int r,int g,int b){
 int x,y,cx,cy,inside;
 for(y=y0;y<=y1;y++)for(x=x0;x<=x1;x++){
  inside=((x>=x0+rr&&x<=x1-rr)||(y>=y0+rr&&y<=y1-rr));
  if(!inside){
   cx=(x<x0+rr)?x0+rr:x1-rr;cy=(y<y0+rr)?y0+rr:y1-rr;
   inside=((x-cx)*(x-cx)+(y-cy)*(y-cy)<=rr*rr);
  }
  if(inside&&x>=0&&y>=0&&x<W&&y<H){uint8_t*q=im+3*((size_t)y*W+x);q[0]=r;q[1]=g;q[2]=b;}
 }
}
static void controller_art(void){
 /* Exact C port of upstream NESDoor controller_image(): 336 x 136. */
 const int W=336,H=136;uint8_t *im=(uint8_t*)calloc((size_t)W*H*3,1);nd_buf sx={0};
 if(!im)return;
 rrect(im,W,H,4,6,W-5,H-7,34,80,88,116);
 rrect(im,W,H,8,10,W-9,H-11,30,38,42,58);
 rrect(im,W,H,32,55,112,81,4,14,14,18);
 rrect(im,W,H,59,28,85,108,4,14,14,18);
 pxcircle(im,W,H,72,68,7,40,40,48);
 rrect(im,W,H,130,62,166,74,6,105,105,118);
 rrect(im,W,H,180,62,216,74,6,105,105,118);
 pxcircle(im,W,H,250,68,21,130,180,255); pxcircle(im,W,H,250,68,18,70,120,220);
 pxcircle(im,W,H,298,68,21,140,250,150); pxcircle(im,W,H,298,68,18,80,190,90);
 if(sixel_encode_rgb(im,W,H,&sx)){door_printf("\x1b[7;20H");door_write(sx.p,sx.n);nd_buf_free(&sx);}
 free(im);
}
static int controls(const char*fn){
 char t[300];int k;titleof(fn,t,sizeof(t));
 door_printf("\x1b[0m\x1b[2J\x1b[H\x1b[?25l");
 door_printf("\x1b[2;16H\x1b[1;32mHOW TO PLAY:  %.52s",t);
 door_printf("\x1b[4;20H\x1b[1;33mNative Win32 single-player mode");
 controller_art();
 /* Upstream PAD_SPOTS: dpad=72, select=148, start=198, B=250, A=298. */
 door_printf("\x1b[16;16H\x1b[1;33mARROWS/WASD");
 door_printf("\x1b[16;36H\x1b[1;33mTAB");
 door_printf("\x1b[16;47H\x1b[1;33mENTER");
 door_printf("\x1b[16;61H\x1b[1;33mZ");
 door_printf("\x1b[16;68H\x1b[1;33mX/SPACE");
 door_printf("\x1b[17;20H\x1b[0;37mD-PAD");
 door_printf("\x1b[17;34H\x1b[0;37mSELECT");
 door_printf("\x1b[17;46H\x1b[0;37mSTART");
 door_printf("\x1b[17;61H\x1b[0;37mB");
 door_printf("\x1b[17;71H\x1b[0;37mA");
 door_printf("\x1b[19;10H\x1b[1;31mIn the game:  Ctrl+Q or Esc Esc = back to the list     M = sound");
 door_printf("\x1b[21;10H\x1b[0;36mCtrl+S saves your spot, Ctrl+L loads it. Leaving saves automatically.");
 door_printf("\x1b[23;20H\x1b[1;36mENTER = 1 player      Q = back to the list");
 for(;;){k=key(300);if(k<0)return 0;if(k==13)return 1;if(k=='q'||k=='Q'||k==17)return 0;}
}
static void savenames(const char*fn,char*state,char*srm){
 char base[MAX_PATH],user[80],dir[MAX_PATH];size_t i;
 titleof(fn,base,sizeof(base));
 snprintf(user,sizeof(user),"%s",door_alias());
 if(!user[0])snprintf(user,sizeof(user),"Player");
 for(i=0;base[i];i++)
  if(!isalnum((unsigned char)base[i])&&base[i]!='-'&&base[i]!='_')base[i]='_';
 for(i=0;user[i];i++)
  if(!isalnum((unsigned char)user[i])&&user[i]!='-'&&user[i]!='_')user[i]='_';
 CreateDirectoryA("saves",NULL);
 snprintf(dir,sizeof(dir),"saves\\%s",user);
 CreateDirectoryA(dir,NULL);
 snprintf(state,MAX_PATH,"%s\\%s.state",dir,base);
 snprintf(srm,MAX_PATH,"%s\\%s.srm",dir,base);
}
static void status(const char*s){door_printf("\x1b[1;1H\x1b[2K\x1b[1;33m%s\x1b[0m",s);}
/* Runtime tuning. Defaults reproduce the known-good v0.10 exactly. */
static int cfg_dpad_frames=20;
static int cfg_button_frames=20;
static int cfg_start_frames=6;
static int cfg_select_frames=6;
static int cfg_render_fps=20;

static int ini_int(const char *section,const char *key,int defv,int lo,int hi){
 int v=(int)GetPrivateProfileIntA(section,key,defv,".\\nesdoor-c.ini");
 if(v<lo||v>hi)return defv;
 return v;
}
static void load_tuning(void){
 cfg_dpad_frames=ini_int("input","dpad_hold_frames",20,1,120);
 cfg_button_frames=ini_int("input","button_hold_frames",20,1,120);
 cfg_start_frames=ini_int("input","start_hold_frames",6,1,120);
 cfg_select_frames=ini_int("input","select_hold_frames",6,1,120);
 cfg_render_fps=ini_int("video","render_fps",20,1,60);
}

/* Independent fallback hold timers, one per NES controller button. */
static int hold_up=0,hold_down=0,hold_left=0,hold_right=0;
static int hold_a=0,hold_b=0,hold_start=0,hold_select=0;

static void rebuild_pad(void){
 unsigned v=0;
 if(hold_up)     v|=1u<<RETRO_DEVICE_ID_JOYPAD_UP;
 if(hold_down)   v|=1u<<RETRO_DEVICE_ID_JOYPAD_DOWN;
 if(hold_left)   v|=1u<<RETRO_DEVICE_ID_JOYPAD_LEFT;
 if(hold_right)  v|=1u<<RETRO_DEVICE_ID_JOYPAD_RIGHT;
 if(hold_a)      v|=1u<<RETRO_DEVICE_ID_JOYPAD_A;
 if(hold_b)      v|=1u<<RETRO_DEVICE_ID_JOYPAD_B;
 if(hold_start)  v|=1u<<RETRO_DEVICE_ID_JOYPAD_START;
 if(hold_select) v|=1u<<RETRO_DEVICE_ID_JOYPAD_SELECT;
 pad=v;
}

static void clear_input_holds(void){
 hold_up=hold_down=hold_left=hold_right=0;
 hold_a=hold_b=hold_start=hold_select=0;
 pad=0;
}

/*
 * ANSI byte-mode fallback.  Each button owns its own hold timer, so action
 * keys no longer replace directional state.  This permits combinations such
 * as Right+A and Right+B+A when terminal key events overlap/repeat.
 */
static void input_tick(nd_audio*a,nd_core*c,const char*st,const char*sr){
 static int escn=0;
 static DWORD esclast=0;
 int k=key(0);
 DWORD now=GetTickCount();

 if(escn&&now-esclast>600)escn=0;
 if(k<=0)return;

 if(k==27){
  escn++;esclast=now;
  if(escn>=2){quit_game=1;escn=0;clear_input_holds();}
  return;
 }
 escn=0;

 /* Directions: long enough to bridge normal terminal auto-repeat delay. */
 if(k==1001||k=='w'||k=='W') hold_up=cfg_dpad_frames;
 else if(k==1002||k=='s'||k=='S') hold_down=cfg_dpad_frames;
 else if(k==1003||k=='d'||k=='D') hold_right=cfg_dpad_frames;
 else if(k==1004||k=='a'||k=='A') hold_left=cfg_dpad_frames;

 /* A/B get a longer hold for variable-height jumps and running actions. */
 else if(k=='x'||k=='X'||k==' ') hold_a=cfg_button_frames;
 else if(k=='z'||k=='Z') hold_b=cfg_button_frames;
 else if(k==13) hold_start=cfg_start_frames;
 else if(k==9) hold_select=cfg_select_frames;
 else if(k==17){quit_game=1;clear_input_holds();return;}
 else if(k=='m'||k=='M'){status(audio_toggle(a)?"SOUND ON":"SOUND OFF");return;}
 else if(k==19){status(core_save_state(c,st)?"Game saved - Ctrl+L loads it":"Couldn't save game");return;}
 else if(k==12){status(core_load_state(c,st)?"Loaded your saved spot":"No saved spot yet");return;}
 else if(k==18){core_reset(c);clear_input_holds();status("RESET pressed - saves untouched");return;}

 rebuild_pad();
 (void)sr;
}

/* Age every asserted button independently on each native NES frame. */
static void input_after_frame(void){
 if(hold_up>0)hold_up--;
 if(hold_down>0)hold_down--;
 if(hold_left>0)hold_left--;
 if(hold_right>0)hold_right--;
 if(hold_a>0)hold_a--;
 if(hold_b>0)hold_b--;
 if(hold_start>0)hold_start--;
 if(hold_select>0)hold_select--;
 rebuild_pad();
}
static int play(const char*fn){char path[MAX_PATH],st[MAX_PATH],sr[MAX_PATH],title[300];nd_core*c;nd_audio*a;LARGE_INTEGER fq,last,now;double step,acc=0;nd_buf sx={0};int w=0,h=0,frames=0;int16_t aud[8192*2];size_t an;snprintf(path,sizeof(path),"roms\\%s",fn);savenames(fn,st,sr);c=core_open("fceumm_libretro.dll",path);if(!c){door_printf("\x1b[2J\x1b[H\x1b[1;31mCORE LOAD FAILED\r\n%s",core_last_error());Sleep(3000);return 0;}core_load_sram(c,sr);a=audio_create(core_sample_rate(c),16000,70,2.5,0.2);titleof(fn,title,sizeof(title));door_printf("\x1b[0m\x1b[2J\x1b[H\x1b[?25l");
 door_printf("\x1b[1;12H\x1b[1;32m%.55s",title);
 door_printf("\x1b[4;1H\x1b[1;32mCONTROLS");
 door_printf("\x1b[6;1H\x1b[1;37mArrows\x1b[7;1Hor WASD\x1b[8;2H\x1b[0;37mmove");
 door_printf("\x1b[10;1H\x1b[1;37mX / Space\x1b[11;2H\x1b[0;37mA button");
 door_printf("\x1b[13;1H\x1b[1;37mZ\x1b[14;2H\x1b[0;37mB button");
 door_printf("\x1b[16;1H\x1b[1;37mEnter\x1b[17;2H\x1b[0;37mStart");
 door_printf("\x1b[19;1H\x1b[1;37mTab\x1b[20;2H\x1b[0;37mSelect");
 door_printf("\x1b[4;71H\x1b[1;31mTO QUIT");
 door_printf("\x1b[6;71H\x1b[1;37mCtrl+Q");
 door_printf("\x1b[7;71H\x1b[0;37mor");
 door_printf("\x1b[8;71H\x1b[1;37mEsc Esc");
 door_printf("\x1b[11;71H\x1b[1;32mSOUND");
 door_printf("\x1b[12;71H\x1b[1;37mM on/off");
 door_printf("\x1b[15;71H\x1b[1;32mSAVE");
 door_printf("\x1b[16;71H\x1b[1;37mCtrl+S");
 door_printf("\x1b[18;71HCtrl+L");
 door_printf("\x1b[20;71HCtrl+R");
 QueryPerformanceFrequency(&fq);QueryPerformanceCounter(&last);
 step=1.0/core_fps(c);quit_game=0;clear_input_holds();
 {
  double render_step=1.0/(double)cfg_render_fps,render_acc=render_step;
  while(!quit_game&&door_connected()){
   QueryPerformanceCounter(&now);
   {double dt=(double)(now.QuadPart-last.QuadPart)/fq.QuadPart;last=now;
    if(dt<0)dt=0;if(dt>0.100)dt=0.100;acc+=dt;render_acc+=dt;}
   input_tick(a,c,st,sr);core_set_pad(c,pad);
   {int ran=0;while(acc>=step&&ran<5){core_run(c);input_after_frame();core_set_pad(c,pad);acc-=step;ran++;}
    if(ran==5&&acc>step*5)acc=step;}
   while((an=core_take_audio(c,aud,8192))>0)audio_feed(a,aud,(int)an);
   audio_pump(a);
   if(render_acc>=render_step){
    const uint8_t*rgb=core_rgb(c,&w,&h);render_acc=fmod(render_acc,render_step);
    if(rgb&&sixel_encode_scaled(rgb,w,h,461,360,&sx)){
     door_printf("\x1b[2;12H");
     if(!door_write(sx.p,sx.n)){nd_buf_free(&sx);break;}
     nd_buf_free(&sx);
    }
   }
   if(acc<step){double remain=step-acc;if(remain>0.002)Sleep(1);else Sleep(0);}
  }
 }
core_save_sram(c,sr);core_save_state(c,st);cleanup_log("play: audio_destroy begin");
audio_destroy(a);
cleanup_log("play: audio_destroy end");
cleanup_log("play: core_close begin");
core_close(c);
cleanup_log("play: core_close end");
cleanup_log("play: drain begin");
drain();
cleanup_log("play: returning to ROM menu");
return 1;}
int main(int argc,char**argv){load_tuning();int s;LOGF=fopen("nesdoor-c.log","a");logmsg("NESDoor-C v0.10 configurable start");if(argc<2)return 2;if(!door_open(argv[1])){logmsg("door_open failed");return 3;}scan();for(;;){s=menu();if(s<0)break;if(controls(roms[s]))play(roms[s]);}door_printf("\x1b[0m\x1b[2J\x1b[H\x1b[?25hReturning to BBS...\r\n");cleanup_log("door: door_close begin");
door_close();
cleanup_log("door: door_close end");
if(LOGF)fclose(LOGF);
cleanup_log("door: process return");
return 0;}
