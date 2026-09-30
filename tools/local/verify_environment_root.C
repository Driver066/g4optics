#include <TCanvas.h>
#include <TFile.h>
#include <TH1D.h>
#include <TROOT.h>
#include <TSystem.h>
#include <TTree.h>

#include <fstream>
#include <stdexcept>
#include <string>

// Paths are passed through environment variables, never interpolated as C++.
void verify_environment_root()
{
  const char* input = gSystem->Getenv("G4OPTICS_VERIFY_ROOT_INPUT");
  const char* output = gSystem->Getenv("G4OPTICS_VERIFY_ROOT_OUTPUT");
  if (!input || !output) throw std::runtime_error("Missing verification paths");
  TFile file(input, "READ");
  if (file.IsZombie()) throw std::runtime_error("Cannot read ROOT input");
  TTree* tree = nullptr;
  file.GetObject("scan", tree);
  if (!tree || !tree->GetBranch("sipm_detected_photons"))
    throw std::runtime_error("Missing scan tree or photon-count branch");
  const auto entries = tree->GetEntries();
  if (entries <= 0) throw std::runtime_error("Empty scan tree");
  const auto maximum = tree->GetMaximum("sipm_detected_photons");
  TH1D histogram("environment_photons", "SiPM photons per event;Collected photons;Events",
                 40, 0., maximum > 0. ? maximum + 1. : 1.);
  const auto drawn = tree->Draw("sipm_detected_photons>>environment_photons", "", "goff");
  if (drawn != entries || histogram.GetEntries() != entries)
    throw std::runtime_error("ROOT histogram entry mismatch");
  TCanvas canvas("environment_canvas", "Environment verification", 1000, 700);
  histogram.SetLineColor(kBlue + 1);
  histogram.SetLineWidth(2);
  histogram.Draw("HIST");
  const std::string prefix(output);
  canvas.SaveAs((prefix + ".png").c_str());
  canvas.SaveAs((prefix + ".pdf").c_str());
  std::ofstream receipt(prefix + ".json");
  receipt << "{\"root_version\":\"" << gROOT->GetVersion()
          << "\",\"tree_entries\":" << entries
          << ",\"drawn_entries\":" << drawn
          << ",\"histogram_entries\":" << histogram.GetEntries() << "}\n";
  if (!receipt) throw std::runtime_error("Cannot write ROOT receipt");
}
