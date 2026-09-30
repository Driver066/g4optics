#ifndef PaintedCornerRule_h
#define PaintedCornerRule_h 1
#include <array>
#include <cmath>

// Pure geometry rule, shared with the standalone covariance test.
namespace PaintedCornerRule {
using Point = std::array<double,3>;
inline int Faces(const Point& p,const Point& half,double tolerance) {
  int mask=0,count=0;
  for(int a=0;a<3;++a) if(std::abs(std::abs(p[a])-half[a])<=tolerance) { mask|=1<<a; ++count; }
  return count>=2?mask:0;
}
inline Point Inset(Point p,const Point& half,double tolerance,int scale,int mask) {
  for(int a=0;a<3;++a) if(mask&(1<<a)) p[a]=std::copysign(half[a]-scale*tolerance,p[a]);
  return p;
}
}
#endif
