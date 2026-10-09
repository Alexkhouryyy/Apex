"""HGP threshold formula; extracted method verbatim, persistence omitted. See NOTICE."""
import math

class Threshold:
    def get_current_threshold(self) -> float:
        t = self.state["iterations"]
        tau_t = self.tau_0 * math.exp(-self.beta * t)
        tau_t = max(self.tau_min, tau_t)
        
        return round(tau_t, 4)
