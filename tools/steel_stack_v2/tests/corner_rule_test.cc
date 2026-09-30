#include "PaintedCornerRule.hh"
#include <algorithm>
#include <cassert>
#include <iostream>
int main() {
  using namespace PaintedCornerRule;
  const double tau=1.e-9;
  Point h={50.,50.,12.};
  long checked=0;
  for(int scale:{16,32,64}) for(int mask:{3,5,6,7}) {
    Point p={2.,3.,4.};
    for(int a=0;a<3;++a) if(mask&(1<<a)) p[a]=h[a]-.25*tau;
    assert(Faces(p,h,tau)==mask);
    auto q=Inset(p,h,tau,scale,mask);
    std::array<int,3> permutation={0,1,2};
    do {
      for(int signs=0;signs<8;++signs) {
        Point hp,pp,expected;
        for(int a=0;a<3;++a) {
          int sign=(signs&(1<<a))?-1:1, old=permutation[a];
          hp[a]=h[old];pp[a]=sign*p[old];expected[a]=sign*q[old];
        }
        auto actual=Inset(pp,hp,tau,scale,Faces(pp,hp,tau));
        assert(actual==expected);
        double norm2=0.;
        for(int a=0;a<3;++a) {
          assert(std::abs(actual[a])<hp[a]);
          norm2+=(actual[a]-pp[a])*(actual[a]-pp[a]);
        }
        assert(norm2<=3.*(scale+1)*(scale+1)*tau*tau);
        ++checked;
      }
    } while(std::next_permutation(permutation.begin(),permutation.end()));
  }
  assert(Faces({50.,0.,0.},h,tau)==0);
  assert(Faces({50.,50.-4*tau,0.},h,tau)==0);
  assert(Faces({50.,50.-32*tau,0.},h,tau)==0);
  std::cout << checked << " actual-rule reflection/permutation checks passed\n";
}
