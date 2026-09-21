// Feedback-calibrated two-round beam search. Conservative physical diameter.
// Per-entry knife lookup and scoring mass are supplied by Python from raw CSV.
//
// fast2: beam reduction replaced by "bucket-dedup first (open-addressing hash),
// then sort only the distinct buckets". The previous engine sorted all ~304
// candidates then deduped by a linear scan; the number of DISTINCT buckets is
// only ~54, so the sort shrinks by ~5.7x. Selection semantics are intended to be
// identical: the old code, having sorted by rank, kept the first occurrence of
// each bucket key (= the minimum-rank member of that bucket), and the order of
// the surviving buckets was ascending minimum rank. The new code keeps the
// minimum-rank member per bucket in a hash table and then sorts the survivors by
// rank, which reproduces both properties.
//
// Build with -DXCHECK to additionally run the ORIGINAL reduction on the same
// candidate set and compare the two survivor vectors field-by-field. That is the
// equivalence probe used for the KH_MAX_ATTEMPTS regression.
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <string>
#include <vector>
using namespace std;
struct Order{int q,cap,pm,delivered=0;double size,mu,scoring_mu;vector<int> costs;};
struct Row{int p,nb;vector<pair<int,int>> cuts;};
struct Batch{int bid;vector<int> ids;vector<Row> rows;};
struct Tot{double k=0,f=0,r=0;};
Tot operator+(Tot a,Tot b){return {a.k+b.k,a.f+b.f,a.r+b.r};}
Tot operator-(Tot a,Tot b){return {a.k-b.k,a.f-b.f,a.r-b.r};}
struct State{double l1=0,l2=0,f=0,rank=0;int k=0;array<short,24>a{},b{};};
struct Cand{double r;int i;long long key;};   // rank + index + precomputed bucket key
vector<Order> orders;vector<double> blanks;vector<Batch>batches;
int ceilnear(double x){return int(ceil(x-1e-9));}
Tot value(const Row&r,int bid){Tot t;double L=0;for(auto [i,k]:r.cuts){t.k+=orders[i].costs.at(k);L+=k*orders[i].size;}t.f=L*r.p*orders[r.cuts[0].first].scoring_mu;t.r=r.nb*blanks[bid];return t;}
// The 2026-09-16 98.9300 feedback settled the cap: the knife subscore is capped at
// 100 inside the computation, so for any knife count <= 90000 the total is exactly
//   70 + 30 * (finished/raw)
// i.e. knives below 90000 are worth NOTHING and the only lever is yield. The budget
// is a hard constraint, not a soft preference.
const double KNIFE_BUDGET=89990.;  // leave margin for the 50 m guard segments added on export
double total_of(Tot t){return 40*min(1.,90000/t.k)+30*t.f/t.r+30.;}
bool better(Tot c,Tot b){
 if(c.k>KNIFE_BUDGET)return false;
 if(b.k>KNIFE_BUDGET)return true;
 return c.f/c.r>b.f/b.r+1e-12;}
static inline long long bucket_key(const State&s){return ((long long)(int)(s.l1*2)<<32)^(unsigned)(int)(s.l2*2);}
int main(int argc,char**argv){
 if(argc!=6)return 2;
 ifstream in(argv[1]);int no,nb,nba;in>>no>>nb>>nba;orders.resize(no);blanks.resize(nb+1);batches.resize(nba);
 for(auto&o:orders){int nc;in>>o.q>>o.cap>>o.size>>o.mu>>o.pm>>o.scoring_mu>>nc;o.costs.resize(nc);for(int&v:o.costs)in>>v;}
 for(int j=1;j<=nb;j++)in>>blanks[j];
 Tot total;
 for(auto&b:batches){int ni,nr;in>>b.bid>>ni>>nr;b.ids.resize(ni);for(int&i:b.ids)in>>i;b.rows.resize(nr);
  for(auto&r:b.rows){int n;in>>r.p>>r.nb>>n;r.cuts.resize(n);for(auto&[i,k]:r.cuts){in>>i>>k;orders[i].delivered+=k*r.p;}total=total+value(r,b.bid);}}
 if(!in)return 3;
 vector<Cand>candBuf;vector<State>keptBuf;vector<Cand>survBuf;
 vector<long long>hkey;vector<unsigned>hstamp;vector<int>hslot;size_t hcap=0;unsigned hgen=0;int hshift=0;
#ifdef XCHECK
 long long xcRed=0,xcBad=0,xcSizeBad=0,xcFBad=0,xcKBad=0,xcGeoBad=0,xcKWorse=0;int badStateSample=0;
#endif
 double seconds=stod(argv[3]);
 long long nExp=0,nSortItems=0,nSorts=0,nBuckets=0;double tExp=0,tSort=0,tDedup=0,tEv=0;long long nEval=0;
 mt19937 rng(stoul(argv[4]));int beam=stoi(argv[5]);long attempts=0,accepted=0;
 const char* __cap=getenv("KH_MAX_ATTEMPTS");long long maxAttempts=__cap?atoll(__cap):0;
 auto started=chrono::steady_clock::now();
 auto save=[&](){ofstream out(string(argv[2])+".tmp");out<<batches.size()<<'\n';for(auto&b:batches){out<<b.rows.size()<<'\n';for(auto&r:b.rows){out<<r.p<<' '<<r.nb<<' '<<r.cuts.size();for(auto[i,k]:r.cuts)out<<' '<<i<<' '<<k;out<<'\n';}}out.close();remove(argv[2]);rename((string(argv[2])+".tmp").c_str(),argv[2]);};
 while(chrono::duration<double>(chrono::steady_clock::now()-started).count()<seconds
       && (!maxAttempts||attempts<maxAttempts)){
  auto&batch=batches[rng()%nba];if(batch.rows.size()<2)continue;
  int u=rng()%batch.rows.size(),v=rng()%batch.rows.size();if(u==v)continue;
  Row old1=batch.rows[u],old2=batch.rows[v];map<int,int> quantities;
  for(auto[i,k]:old1.cuts)quantities[i]+=k*old1.p;for(auto[i,k]:old2.cuts)quantities[i]+=k*old2.p;
  if(quantities.size()>24)continue;attempts++;
  vector<int>ids;for(auto[i,q]:quantities)ids.push_back(i);
  sort(ids.begin(),ids.end(),[](int i,int j){return orders[i].size>orders[j].size;});
  double mu=orders[ids[0]].mu,bw=blanks[batch.bid],usable=bw/mu;
  int pmax=min(orders[ids[0]].pm,int(floor(60000/(50*mu)+1e-9)));
  vector<int>ps={old1.p,old2.p,pmax,max(1,pmax-1),max(1,pmax-2),max(1,pmax-3),max(1,pmax-int(rng()%max(1,pmax/4)))};
  sort(ps.begin(),ps.end());ps.erase(unique(ps.begin(),ps.end()),ps.end());
  Tot old=value(old1,batch.bid)+value(old2,batch.bid),best_total=total;Row best1,best2;bool found=false;
  for(int trial=0;trial<8;trial++){
   int p1=trial==0?old1.p:ps[rng()%ps.size()],p2=trial==0?old2.p:ps[rng()%ps.size()];
   double cap1=min(150.,60000/(p1*mu))-2,cap2=min(150.,60000/(p2*mu))-2;
   // Yield-only objective: raw waste dominates, and the knife count is a hard
   // budget rather than a weighted term, because a knife costs nothing while the
   // plan stays under 90000 cuts.
    vector<State>states(1);double lambda=1.;
   vector<State>next;states.reserve(beam);next.reserve((size_t)beam*8);
   for(int t=0;t<(int)ids.size()&&!states.empty();t++){
    int i=ids[t];auto&o=orders[i];int other=o.delivered-quantities[i],lo=max(0,o.q-other),hi=o.cap-other;
    struct Option{int a,b;double l1,l2,f;};vector<Option>options;
    int amax=min(int(floor(min(cap1,usable-2)/o.size+1e-9)),hi/p1);
    for(int a=0;a<=amax;a++){
     int blo=max(0,(lo-a*p1+p2-1)/p2),bhi=min((hi-a*p1)/p2,int(floor(min(cap2,usable-2)/o.size+1e-9)));
     for(int b=blo;b<=bhi;b++)options.push_back({a,b,a*o.size,b*o.size,(a*p1+b*p2)*o.size*o.scoring_mu});
    }
    next.clear();next.reserve(states.size()*options.size());
    candBuf.clear();candBuf.reserve(states.size()*options.size());
    auto __b0=chrono::steady_clock::now();
    for(auto&s:states)for(auto&op:options){
     if(s.l1+op.l1>cap1+1e-8||s.l2+op.l2>cap2+1e-8)continue;
     State n=s;n.l1+=op.l1;n.l2+=op.l2;n.f+=op.f;n.k+=o.costs[op.a]+o.costs[op.b];n.a[t]=op.a;n.b[t]=op.b;
     double raw=(ceilnear((n.l1+2)*p1*mu/bw)+ceilnear((n.l2+2)*p2*mu/bw))*bw;
     n.rank=lambda*(raw-n.f/.96)+1e5*max(0.,double(n.k)-KNIFE_BUDGET);
     nExp++;next.push_back(n);candBuf.push_back({n.rank,(int)next.size()-1,bucket_key(n)});
    }
    tExp+=chrono::duration<double>(chrono::steady_clock::now()-__b0).count();
    if(next.size()>(size_t)beam){nSorts++;nSortItems+=next.size();
     // ---- new reduction: hash-dedup by bucket (keep min rank), then sort survivors ----
     size_t need=1;while(need<next.size())need<<=1;need<<=1;   // >= 2 * candidates
     if(need>hcap){hcap=need;hkey.assign(hcap,-1);hstamp.assign(hcap,0u);hslot.assign(hcap,0);hgen=0;hshift=0;while(((size_t)1<<hshift)<hcap)hshift++;}
     if(++hgen==0){fill(hstamp.begin(),hstamp.end(),0u);hgen=1;}
     const size_t hmask=hcap-1;
     auto __q1=chrono::steady_clock::now();
     survBuf.clear();
     for(const Cand&c:candBuf){
      long long k=c.key;
      size_t j=(size_t)(((unsigned long long)k*11400714819323198485ull)>>(64-hshift))&hmask;
      while(hstamp[j]==hgen&&hkey[j]!=k)j=(j+1)&hmask;
      if(hstamp[j]!=hgen){hstamp[j]=hgen;hkey[j]=k;hslot[j]=(int)survBuf.size();survBuf.push_back(c);}
      // Canonical representative: minimum rank, then FEWEST KNIVES, then lowest index.
      // Members of one bucket share the same rank (and, because scoring_mu is uniform,
      // the same length term), so they are yield-equivalent; the only real difference is
      // the knife count. Preferring the cheapest member therefore preserves the objective
      // while never spending more knives than the previous arbitrary pick.
      else{
       Cand&bb=survBuf[hslot[j]];
       if(c.r<bb.r||(c.r==bb.r&&(next[c.i].k<next[bb.i].k||(next[c.i].k==next[bb.i].k&&c.i<bb.i))))bb=c;
      }
     }
     nBuckets+=survBuf.size();
     tDedup+=chrono::duration<double>(chrono::steady_clock::now()-__q1).count();
     auto __q2=chrono::steady_clock::now();
     sort(survBuf.begin(),survBuf.end(),[](const Cand&a,const Cand&b){return a.r<b.r||(a.r==b.r&&a.i<b.i);});
     tSort+=chrono::duration<double>(chrono::steady_clock::now()-__q2).count();
     size_t lim=survBuf.size()<(size_t)beam?survBuf.size():(size_t)beam;
     keptBuf.clear();
     for(size_t z=0;z<lim;z++)keptBuf.push_back(next[survBuf[z].i]);
#ifdef XCHECK
     // ---- original reduction, for equivalence probing ----
     sort(candBuf.begin(),candBuf.end(),[](const Cand&a,const Cand&b){return a.r<b.r;});
     static vector<State> xcOld;xcOld.clear();
     long long bkeys[80];int bn=0;
     for(const Cand&c:candBuf){
      const State&s=next[c.i];
      long long key=bucket_key(s);
      bool dup=false;for(int j2=0;j2<bn;j2++)if(bkeys[j2]==key){dup=true;break;}
      if(dup)continue;
      bkeys[bn++]=key;xcOld.push_back(s);
      if(xcOld.size()>=(size_t)beam)break;
     }
     xcRed++;
     if(xcOld.size()!=keptBuf.size()){xcSizeBad++;
      if(badStateSample<8){badStateSample++;cerr<<"XSIZE attempt="<<attempts<<" t="<<t<<" n="<<next.size()<<" cand="<<candBuf.size()<<" surv="<<survBuf.size()<<" old="<<xcOld.size()<<" new="<<keptBuf.size()<<"\n";}
     }
     else{
      bool same=true;size_t at=0;
      for(size_t z=0;z<xcOld.size();z++){
       const State&A=xcOld[z],&B=keptBuf[z];
       if(A.l1!=B.l1||A.l2!=B.l2||A.f!=B.f||A.k!=B.k||A.a!=B.a||A.b!=B.b){same=false;at=z;break;}
      }
      if(!same){xcBad++;
       if(xcOld[at].f!=keptBuf[at].f)xcFBad++;
       if(xcOld[at].k!=keptBuf[at].k)xcKBad++;
       if(xcOld[at].l1!=keptBuf[at].l1||xcOld[at].l2!=keptBuf[at].l2)xcGeoBad++;
       if(keptBuf[at].k>xcOld[at].k)xcKWorse++;
       if(badStateSample<6){badStateSample++;cerr<<"XCONTENT attempt="<<attempts<<" t="<<t<<" n="<<next.size()<<" surv="<<survBuf.size()<<" at="<<at
        <<" oldf="<<xcOld[at].f<<" newf="<<keptBuf[at].f<<" oldk="<<xcOld[at].k<<" newk="<<keptBuf[at].k<<"\n";}
      }
     }
#endif
     next.swap(keptBuf);
    }
    states.swap(next);
   }
   // Scoring scan over the beam. The two candidate rows are evaluated straight
   // from the state instead of materialising a Row (and its vector allocations)
   // for every state; the Row objects are only built for the state that actually
   // wins. The arithmetic is ordered exactly as value(r1)+value(r2) was, so this
   // is an equivalence-preserving refactor.
   int nids=(int)ids.size();
   auto __e0=chrono::steady_clock::now();
   for(auto&s:states){
    if(s.l1<48-1e-8||s.l2<48-1e-8)continue;
    int nb1=ceilnear((s.l1+2)*p1*mu/bw),nb2=ceilnear((s.l2+2)*p2*mu/bw);
    double L1=0,L2=0;int kk1=0,kk2=0;int f1=-1,f2=-1;
    for(int t=0;t<nids;t++){
     int av=s.a[t];if(av){if(f1<0)f1=ids[t];kk1+=orders[ids[t]].costs[av];L1+=av*orders[ids[t]].size;}
     int bv=s.b[t];if(bv){if(f2<0)f2=ids[t];kk2+=orders[ids[t]].costs[bv];L2+=bv*orders[ids[t]].size;}
    }
    Tot v1,v2;v1.k=kk1;v2.k=kk2;
    v1.f=L1*p1*orders[f1].scoring_mu;v2.f=L2*p2*orders[f2].scoring_mu;
    v1.r=nb1*blanks[batch.bid];v2.r=nb2*blanks[batch.bid];
    Tot candidate=total-old+v1+v2;
    if(better(candidate,best_total)){
     found=true;best_total=candidate;
     Row r1,r2;r1.p=p1;r2.p=p2;r1.nb=nb1;r2.nb=nb2;
     r1.cuts.reserve(nids);r2.cuts.reserve(nids);
     for(int t=0;t<nids;t++){if(s.a[t])r1.cuts.emplace_back(ids[t],s.a[t]);if(s.b[t])r2.cuts.emplace_back(ids[t],s.b[t]);}
     best1=r1;best2=r2;
    }
   }
   tEv+=chrono::duration<double>(chrono::steady_clock::now()-__e0).count();
   nEval+=states.size();
  }
  if(found){
   for(auto[i,q]:quantities)orders[i].delivered-=q;
   for(auto[i,k]:best1.cuts)orders[i].delivered+=k*best1.p;for(auto[i,k]:best2.cuts)orders[i].delivered+=k*best2.p;
   batch.rows[u]=best1;batch.rows[v]=best2;total=best_total;accepted++;
   if(accepted%100==0){save();cout<<setprecision(12)<<"attempts="<<attempts<<" accepted="<<accepted<<" knives="<<total.k<<" yield="<<total.f/total.r<<" total_if_capped="<<min(100.,total_of(total))<<endl;}
  }
 }
 save();cout<<setprecision(12)<<"FINAL attempts="<<attempts<<" accepted="<<accepted<<" knives="<<total.k<<" yield="<<total.f/total.r<<" total_if_capped="<<min(100.,total_of(total))<<endl;
#ifdef XCHECK
 cerr<<"XCHECK reductions="<<xcRed<<" sizeBad="<<xcSizeBad<<" contentBad="<<xcBad<<" fDiff="<<xcFBad<<" kDiff="<<xcKBad<<" bucketDiff="<<xcGeoBad<<" knivesWorse="<<xcKWorse<<"\n";
#endif
 cerr<<"PROF attempts="<<attempts<<" expPerAttempt="<<(nExp/(double)attempts)<<" avgSortItems="<<(nSorts?(double)nSortItems/nSorts:0.)
     <<" avgBuckets="<<(nSorts?(double)nBuckets/nSorts:0.)<<"\n"
     <<"     expansionSec="<<tExp<<" ("<<(tExp/seconds*100)<<"%)  hashSec="<<tDedup<<" ("<<(tDedup/seconds*100)<<"%)  sortSec="<<tSort
     <<" ("<<(tSort/seconds*100)<<"%)  evalSec="<<tEv<<" ("<<(tEv/seconds*100)<<"%)  other="<<(seconds-tExp-tDedup-tSort-tEv)<<"\n";
 for(auto&o:orders)if(o.delivered<o.q||o.delivered>o.cap)return 4;
 return 0;
}
