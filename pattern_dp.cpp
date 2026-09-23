// Unbounded integer production DP. Each case has a fixed order specification
// and blank type. Patterns are independently generated/validated in Python.
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <vector>
struct Pattern { int n,k,p,b,cuts; double raw; };
int main(int argc, char** argv) {
    if (argc != 4) return 2;
    std::ifstream in(argv[1]); std::ofstream out(argv[2]);
    double lambda=std::stod(argv[3]); int cases; in>>cases;
    out<<cases<<'\n';
    for(int c=0;c<cases;++c) {
        int tag, bid, maxq, np, nq; in>>tag>>bid>>maxq>>np>>nq;
        std::vector<Pattern> patterns(np);
        for(auto& r:patterns) in>>r.n>>r.k>>r.p>>r.b>>r.cuts>>r.raw;
        std::vector<int> queries(nq), caps(nq); for(int i=0;i<nq;++i) in>>queries[i]>>caps[i];
        std::vector<double> costs(maxq+1,1e100);
        std::vector<int> bestPattern(maxq+1,-1);
        // Identical quantities only need the best scalar-cost pattern.
        for(int j=0;j<np;++j) {
            auto r=patterns[j]; if(r.n>maxq) continue;
            double value=r.cuts+lambda*r.raw;
            if(value<costs[r.n]) {costs[r.n]=value;bestPattern[r.n]=j;}
        }
        std::vector<int> ids;
        for(int q=1;q<=maxq;++q) if(bestPattern[q]>=0) ids.push_back(bestPattern[q]);
        std::vector<double> dp(maxq+1,1e100); std::vector<int> parent(maxq+1,-1);
        dp[0]=0;
        for(int q=1;q<=maxq;++q) {
            for(int j:ids) {
                auto& r=patterns[j]; if(r.n>q) break;
                double value=dp[q-r.n]+r.cuts+lambda*r.raw;
                if(value+1e-10<dp[q]) {dp[q]=value;parent[q]=j;}
            }
        }
        out<<tag<<' '<<bid<<' '<<nq<<'\n';
        for(int qi=0;qi<nq;++qi) {
            int q=queries[qi], target=q;
            for(int z=q+1;z<=caps[qi];++z) if(dp[z]+1e-10<dp[target]) target=z;
            std::vector<int> rows;int rem=target;
            while(rem>0 && parent[rem]>=0) {int j=parent[rem];rows.push_back(j);rem-=patterns[j].n;}
            if(rem!=0) {out<<q<<" -1\n";continue;}
            out<<q<<' '<<rows.size(); for(int j:rows) out<<' '<<j;out<<'\n';
        }
    }
    return in && out ? 0 : 3;
}
