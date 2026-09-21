// Feedback-calibrated two-round beam search. Conservative physical diameter.
// Per-entry knife lookup and scoring mass are supplied by Python from raw CSV.
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
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
vector<Order> orders;vector<double> blanks;vector<Batch>batches;
int ceilnear(double x){return int(ceil(x-1e-9));}
Tot value(const Row&r,int bid){Tot t;double L=0;for(auto [i,k]:r.cuts){t.k+=orders[i].costs.at(k);L+=k*orders[i].size;}t.f=L*r.p*orders[r.cuts[0].first].scoring_mu;t.r=r.nb*blanks[bid];return t;}
double score(Tot t){return 40*min(1.,90000/t.k)+30*t.f/t.r;}
int main(int argc,char**argv){
 if(argc!=6)return 2;
 ifstream in(argv[1]);int no,nb,nba;in>>no>>nb>>nba;orders.resize(no);blanks.resize(nb+1);batches.resize(nba);
 for(auto&o:orders){int nc;in>>o.q>>o.cap>>o.size>>o.mu>>o.pm>>o.scoring_mu>>nc;o.costs.resize(nc);for(int&v:o.costs)in>>v;}
 for(int j=1;j<=nb;j++)in>>blanks[j];
 Tot total;
 for(auto&b:batches){int ni,nr;in>>b.bid>>ni>>nr;b.ids.resize(ni);for(int&i:b.ids)in>>i;b.rows.resize(nr);
  for(auto&r:b.rows){int n;in>>r.p>>r.nb>>n;r.cuts.resize(n);for(auto&[i,k]:r.cuts){in>>i>>k;orders[i].delivered+=k*r.p;}total=total+value(r,b.bid);}}
 if(!in)return 3;
 double seconds=stod(argv[3]);mt19937 rng(stoul(argv[4]));int beam=stoi(argv[5]);long attempts=0,accepted=0;
 auto started=chrono::steady_clock::now();
 auto save=[&](){ofstream out(string(argv[2])+".tmp");out<<batches.size()<<'\n';for(auto&b:batches){out<<b.rows.size()<<'\n';for(auto&r:b.rows){out<<r.p<<' '<<r.nb<<' '<<r.cuts.size();for(auto[i,k]:r.cuts)out<<' '<<i<<' '<<k;out<<'\n';}}out.close();remove(argv[2]);rename((string(argv[2])+".tmp").c_str(),argv[2]);};
 while(chrono::duration<double>(chrono::steady_clock::now()-started).count()<seconds){
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
   vector<State>states(1);double lambda=total.k<=90000 ? .01 : .00025;
   for(int t=0;t<(int)ids.size()&&!states.empty();t++){
    int i=ids[t];auto&o=orders[i];int other=o.delivered-quantities[i],lo=max(0,o.q-other),hi=o.cap-other;
    struct Option{int a,b;double l1,l2,f;};vector<Option>options;
    int amax=min(int(floor(min(cap1,usable-2)/o.size+1e-9)),hi/p1);
    for(int a=0;a<=amax;a++){
     int blo=max(0,(lo-a*p1+p2-1)/p2),bhi=min((hi-a*p1)/p2,int(floor(min(cap2,usable-2)/o.size+1e-9)));
     for(int b=blo;b<=bhi;b++)options.push_back({a,b,a*o.size,b*o.size,(a*p1+b*p2)*o.size*o.scoring_mu});
    }
    vector<State>next;next.reserve(states.size()*options.size());
    for(auto&s:states)for(auto&op:options){
     if(s.l1+op.l1>cap1+1e-8||s.l2+op.l2>cap2+1e-8)continue;
     State n=s;n.l1+=op.l1;n.l2+=op.l2;n.f+=op.f;n.k+=o.costs.at(op.a)+o.costs.at(op.b);n.a[t]=op.a;n.b[t]=op.b;
     double raw=(ceilnear((n.l1+2)*p1*mu/bw)+ceilnear((n.l2+2)*p2*mu/bw))*bw;
     n.rank=n.k+lambda*(raw-n.f/.96);
     // Diversity within the beam: the allocation to the first row must survive.
     next.push_back(n);
    }
    if(next.size()>(size_t)beam){
     sort(next.begin(),next.end(),[](const State&a,const State&b){return a.rank<b.rank;});
     vector<State>kept;set<pair<int,int>>buckets;
     for(auto&s:next){auto key=make_pair(int(s.l1*2),int(s.l2*2));if(buckets.insert(key).second)kept.push_back(s);if(kept.size()>=(size_t)beam)break;}
     next.swap(kept);
    }
    states.swap(next);
   }
   for(auto&s:states){
    if(s.l1<48-1e-8||s.l2<48-1e-8)continue;
    Row r1,r2;r1.p=p1;r2.p=p2;r1.nb=ceilnear((s.l1+2)*p1*mu/bw);r2.nb=ceilnear((s.l2+2)*p2*mu/bw);
    for(int t=0;t<(int)ids.size();t++){if(s.a[t])r1.cuts.emplace_back(ids[t],s.a[t]);if(s.b[t])r2.cuts.emplace_back(ids[t],s.b[t]);}
    Tot candidate=total-old+value(r1,batch.bid)+value(r2,batch.bid);
    if(score(candidate)>score(best_total)+1e-10){found=true;best_total=candidate;best1=r1;best2=r2;}
   }
  }
  if(found){
   for(auto[i,q]:quantities)orders[i].delivered-=q;
   for(auto[i,k]:best1.cuts)orders[i].delivered+=k*best1.p;for(auto[i,k]:best2.cuts)orders[i].delivered+=k*best2.p;
   batch.rows[u]=best1;batch.rows[v]=best2;total=best_total;accepted++;
   if(accepted%100==0){save();cout<<setprecision(12)<<"attempts="<<attempts<<" accepted="<<accepted<<" knives="<<total.k<<" yield="<<total.f/total.r<<" score="<<score(total)+20.*4998/4999+10<<endl;}
  }
 }
 save();cout<<setprecision(12)<<"FINAL attempts="<<attempts<<" accepted="<<accepted<<" knives="<<total.k<<" yield="<<total.f/total.r<<" score="<<score(total)+20.*4998/4999+10<<endl;
 for(auto&o:orders)if(o.delivered<o.q||o.delivered>o.cap)return 4;
 return 0;
}
