// Feedback-calibrated two-round beam search. Conservative physical diameter.
// Per-entry knife lookup and scoring mass are supplied by Python from raw CSV.
//
// fast5: fast4 + parallel trials (OpenMP). BIT-IDENTICAL to fast4.
//
// Where the parallelism is, and why it is safe:
//
//   Each attempt picks a random row pair and then runs 8 independent trials over it.
//   The trials only READ pre-commit state (orders[].delivered, the two rows, `total`)
//   and write their own beam buffer, so they can run concurrently. The one thing that
//   is NOT independent is the scoring fold, because `better()` carries a 1e-12
//   tolerance and is therefore not associative:
//
//       a=0, b=0.6eps, c=1.2eps   ->   fold a,b,c gives c, fold b,a,c gives b
//
//   So the work is split in two:
//
//     phase 1 (parallel)  the eight beam searches - ~97% of the work
//     phase 2 (serial)    the scoring fold, in the ORIGINAL trial order over each
//                         trial's own beam - ~3% of the work
//
//   The random p1/p2 picks are drawn up front, in the exact order the sequential loop
//   would have drawn them, so every trial sees identical inputs.
//
//   Consequence: fast5 reproduces fast4 byte for byte, for any thread count. Verified
//   identical to fast4 at 3000 / 20000 fixed attempts with KH_THREADS = 1 and 8.
//
// There are only 8 independent trials, so `KH_THREADS` above 8 cannot help; the
// default is min(8, hardware threads).
//
// fast4: the beam reduction, rebuilt around what profiling actually showed.
//
//   * Expansion used to materialise a Row (two vector allocations) for EVERY
//     state in the beam just to score it. The rows are now scored straight from
//     the state and only the winning state builds Row objects (fast3 change).
//   * The reduction sorted all candidates and then deduped. Measured: the sort
//     was ~42% of the run. Replaced by an open-addressing hash that keeps the
//     canonical representative per bucket, followed by a partial selection of
//     the best `beam` survivors.
//   * Measured bucket collapse is only ~1.64x (avg 304 candidates -> ~186
//     buckets), NOT the ~5.7x that was assumed earlier, so shrinking the sort
//     input alone bought almost nothing. What does pay off is (a) making the
//     sort element small again, and (b) not fully sorting at all.
//   * The candidate record is 16 bytes (rank, index, knife count) and the bucket
//     key rides in a parallel array, so the sort moves 16 bytes per element and
//     the hash never has to chase a pointer into the 120-byte State array.
//   * Selection uses nth_element on the survivors. (rank,index) is a strict
//     total order, so "the smallest `beam` survivors" is a well-defined set and
//     partial selection returns exactly the same prefix a full sort would; only
//     that prefix is then sorted, because the beam order carries into the next
//     level. Verified byte-identical to fast3 at 3k/20k/60k fixed attempts.
//
// Ranking is untouched: `(l1+2)*p1*mu/bw` still stands exactly as written,
// because ceilnear() rounds on that expression's boundary and an algebraically
// equivalent rewrite can produce a plan with one billet too few.
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <omp.h>
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
struct Option{int a,b;double l1,l2,f;};   // hoisted out of the level loop so its buffer can be reused
struct Cand{double r;int i;int k;};   // 16 B: rank + index + knife count (kept small on purpose:
                                      // the sort only moves 16 bytes and never touches the state)
                                      // the bucket key lives in the parallel candKey array
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
static inline bool rankCand(const Cand&a,const Cand&b){return a.r<b.r||(a.r==b.r&&a.i<b.i);}
int main(int argc,char**argv){
 if(argc!=6)return 2;
 ifstream in(argv[1]);int no,nb,nba;in>>no>>nb>>nba;orders.resize(no);blanks.resize(nb+1);batches.resize(nba);
 for(auto&o:orders){int nc;in>>o.q>>o.cap>>o.size>>o.mu>>o.pm>>o.scoring_mu>>nc;o.costs.resize(nc);for(int&v:o.costs)in>>v;}
 for(int j=1;j<=nb;j++)in>>blanks[j];
 Tot total;
 for(auto&b:batches){int ni,nr;in>>b.bid>>ni>>nr;b.ids.resize(ni);for(int&i:b.ids)in>>i;b.rows.resize(nr);
  for(auto&r:b.rows){int n;in>>r.p>>r.nb>>n;r.cuts.resize(n);for(auto&[i,k]:r.cuts){in>>i>>k;orders[i].delivered+=k*r.p;}total=total+value(r,b.bid);}}
 if(!in)return 3;
 // One persistent beam buffer per trial, so phase 2 can read every trial's survivors.
 vector<vector<State>>tStates(8);
 const char* __th=getenv("KH_THREADS");int nThreads=__th?atoi(__th):0;
 if(nThreads<=0){nThreads=omp_get_max_threads();if(nThreads>8)nThreads=8;}   // only 8 independent trials
 double seconds=stod(argv[3]);mt19937 rng(stoul(argv[4]));int beam=stoi(argv[5]);long attempts=0,accepted=0;
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
  // ---------------------------- parallel trials ----------------------------
  // Draw the random p1/p2 picks for trials 1..7 up front, in exactly the order the
  // sequential loop drew them (trial 0 uses the untouched parallel counts and draws
  // nothing). With those inputs pinned, the eight trials are mutually independent:
  // they read only pre-commit state and write only their own beam buffer.
  int rp1[8],rp2[8],tp1[8],tp2[8];
  for(int trial=1;trial<8;trial++){rp1[trial]=(int)(rng()%ps.size());rp2[trial]=(int)(rng()%ps.size());}
  // Phase 1 - the beam search, ~97% of the work, fanned out over the trials.
  #pragma omp parallel num_threads(nThreads)
  {
  // Per-thread scratch. Declared inside the parallel region so they live on the
  // thread's own stack - NOT thread_local, whose dynamic initialisation and
  // destructor registration made every access in the innermost loops expensive.
  vector<Cand>candBuf;vector<State>keptBuf;vector<Cand>survBuf;vector<long long>candKey;
  vector<long long>hkey;vector<unsigned>hstamp;vector<int>hslot;
  size_t hcap=0;unsigned hgen=0;int hshift=0;
  vector<State>next;vector<Option>options;
  #pragma omp for schedule(dynamic,1)
  for(int trial=0;trial<8;trial++){
   int p1=trial==0?old1.p:ps[rp1[trial]],p2=trial==0?old2.p:ps[rp2[trial]];
   tp1[trial]=p1;tp2[trial]=p2;
   double cap1=min(150.,60000/(p1*mu))-2,cap2=min(150.,60000/(p2*mu))-2;
   // Yield-only objective: raw waste dominates, and the knife count is a hard
   // budget rather than a weighted term, because a knife costs nothing while the
   // plan stays under 90000 cuts.
   vector<State>& states=tStates[trial];states.assign(1,State{});states.reserve(beam);
   double lambda=1.;
   next.reserve((size_t)beam*8);
   for(int t=0;t<(int)ids.size()&&!states.empty();t++){
    int i=ids[t];auto&o=orders[i];int other=o.delivered-quantities[i],lo=max(0,o.q-other),hi=o.cap-other;
    options.clear();
    int amax=min(int(floor(min(cap1,usable-2)/o.size+1e-9)),hi/p1);
    for(int a=0;a<=amax;a++){
     int blo=max(0,(lo-a*p1+p2-1)/p2),bhi=min((hi-a*p1)/p2,int(floor(min(cap2,usable-2)/o.size+1e-9)));
     for(int b=blo;b<=bhi;b++)options.push_back({a,b,a*o.size,b*o.size,(a*p1+b*p2)*o.size*o.scoring_mu});
    }
    next.clear();next.reserve(states.size()*options.size());
    candBuf.clear();candBuf.reserve(states.size()*options.size());
    candKey.clear();candKey.reserve(states.size()*options.size());
    for(auto&s:states)for(auto&op:options){
     if(s.l1+op.l1>cap1+1e-8||s.l2+op.l2>cap2+1e-8)continue;
     State n=s;n.l1+=op.l1;n.l2+=op.l2;n.f+=op.f;n.k+=o.costs[op.a]+o.costs[op.b];n.a[t]=op.a;n.b[t]=op.b;
     double raw=(ceilnear((n.l1+2)*p1*mu/bw)+ceilnear((n.l2+2)*p2*mu/bw))*bw;
     n.rank=lambda*(raw-n.f/.96)+1e5*max(0.,double(n.k)-KNIFE_BUDGET);
     next.push_back(n);candBuf.push_back({n.rank,(int)next.size()-1,n.k});candKey.push_back(bucket_key(n));
    }
    if(next.size()>(size_t)beam){
     // ---- new reduction: hash-dedup by bucket (keep min rank), then sort survivors ----
     size_t need=1;while(need<next.size())need<<=1;need<<=1;   // >= 2 * candidates
     if(need>hcap){hcap=need;hkey.assign(hcap,-1);hstamp.assign(hcap,0u);hslot.assign(hcap,0);hgen=0;hshift=0;while(((size_t)1<<hshift)<hcap)hshift++;}
     if(++hgen==0){fill(hstamp.begin(),hstamp.end(),0u);hgen=1;}
     const size_t hmask=hcap-1;
     survBuf.clear();
     for(size_t ci=0;ci<candBuf.size();ci++){
      const long long k=candKey[ci];
      size_t j=(size_t)(((unsigned long long)k*11400714819323198485ull)>>(64-hshift))&hmask;
      while(hstamp[j]==hgen&&hkey[j]!=k)j=(j+1)&hmask;
      const Cand&c=candBuf[ci];
      if(hstamp[j]!=hgen){hstamp[j]=hgen;hkey[j]=k;hslot[j]=(int)survBuf.size();survBuf.push_back(c);}
      // Canonical representative: minimum rank, then FEWEST KNIVES, then lowest index.
      // Members of one bucket share the same rank (and, because scoring_mu is uniform,
      // the same length term), so they are yield-equivalent; the only real difference is
      // the knife count. Preferring the cheapest member therefore preserves the objective
      // while never spending more knives than the previous arbitrary pick.
      // The knife count travels inside Cand, so this costs no extra memory traffic.
      else{
       Cand&bb=survBuf[hslot[j]];
       if(c.r<bb.r||(c.r==bb.r&&(c.k<bb.k||(c.k==bb.k&&c.i<bb.i))))bb=c;
      }
     }
     // (rank,index) is a STRICT total order, so "the smallest `beam` survivors" is a
     // well-defined set: partial selection is exactly equivalent to a full sort here.
     // nth_element only touches the prefix it needs; the surviving prefix is then sorted
     // because the beam's order is carried into the next level.
     const size_t nsurv=survBuf.size();
     size_t lim=nsurv<(size_t)beam?nsurv:(size_t)beam;
     if(lim<nsurv)nth_element(survBuf.begin(),survBuf.begin()+lim,survBuf.end(),rankCand);
     sort(survBuf.begin(),survBuf.begin()+lim,rankCand);
     keptBuf.clear();
     for(size_t z=0;z<lim;z++)keptBuf.push_back(next[survBuf[z].i]);
     next.swap(keptBuf);
    }
    states.swap(next);
   }
  }   // end omp for
  }   // end omp parallel
  // Phase 2 - the scoring fold, serial ON PURPOSE. better() carries a 1e-12 tolerance,
  // so it is not associative: folding the trials out of order, or folding per-trial
  // maxima together, can pick a different winner than the sequential engine did.
  // Running it in the original trial order, over each trial's own beam, reproduces the
  // sequential result exactly. The scan is only ~3% of the work, so serialising it is
  // nearly free.
  //
  // The candidate rows are evaluated straight from the state instead of materialising a
  // Row (and its vector allocations) for every state; the rows are only built for the
  // state that actually wins. The arithmetic is ordered exactly as value(r1)+value(r2)
  // was, so this is an equivalence-preserving refactor.
  int nids=(int)ids.size();
  for(int trial=0;trial<8;trial++){
   int p1=tp1[trial],p2=tp2[trial];
   for(auto&s:tStates[trial]){
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
  }
  if(found){
   for(auto[i,q]:quantities)orders[i].delivered-=q;
   for(auto[i,k]:best1.cuts)orders[i].delivered+=k*best1.p;for(auto[i,k]:best2.cuts)orders[i].delivered+=k*best2.p;
   batch.rows[u]=best1;batch.rows[v]=best2;total=best_total;accepted++;
   if(accepted%100==0){save();cout<<setprecision(12)<<"attempts="<<attempts<<" accepted="<<accepted<<" knives="<<total.k<<" yield="<<total.f/total.r<<" total_if_capped="<<min(100.,total_of(total))<<endl;}
  }
 }
 save();cout<<setprecision(12)<<"FINAL attempts="<<attempts<<" accepted="<<accepted<<" knives="<<total.k<<" yield="<<total.f/total.r<<" total_if_capped="<<min(100.,total_of(total))<<endl;
 for(auto&o:orders)if(o.delivered<o.q||o.delivered>o.cap)return 4;
 return 0;
}
